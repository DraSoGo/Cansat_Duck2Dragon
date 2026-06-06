#!/usr/bin/env python3
"""Duck2Dragon Monitor - Tkinter dual-LoRa ground station dashboard."""

import csv
import math
import queue
import re
import threading
import time
import tkinter as tk
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any, Callable, Iterable, Optional

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

try:
    import tkintermapview
except Exception:
    tkintermapview = None


CSV_HEADER = (
    "millis,lat,lon,alt_gps,sats,alt_baro,temp,pressure,"
    "ax,ay,az,gx,gy,gz,qw,qx,qy,qz,"
    "high_ax,high_ay,high_az,voltage,current,watt"
)
CSV_FIELDS = CSV_HEADER.split(",")
CSV_FIELD_COUNT = len(CSV_FIELDS)
LOG_TIME_FIELD = "time"
RAW_LOG_HEADER = f"{LOG_TIME_FIELD},raw_line"
MERGED_LOG_HEADER = f"{LOG_TIME_FIELD},{CSV_HEADER},source,rssi,snr"
DEFAULT_BAUD = 115200
DEFAULT_PORT_1 = "/dev/ttyACM0"
DEFAULT_PORT_2 = "/dev/ttyUSB0"
DATA_DIR = Path(__file__).resolve().parent
APP_ICON_PATH = DATA_DIR.parent / "assets" / "D2D_logo.png"
LOG_DIR = DATA_DIR / "logs"
OSM_TILE_CACHE_DIR = LOG_DIR / "map_tiles"
EVENT_DRAIN_BATCH_SIZE = 200
CHART_REFRESH_INTERVAL_SECONDS = 0.25
OSM_TILE_SIZE = 256
OSM_TILE_TIMEOUT_SECONDS = 0.35
OSM_TILE_RETRY_SECONDS = 60.0
OSM_MAX_TILES_PER_REFRESH = 9
OSM_TILE_USER_AGENT = "Duck2DragonMonitor/1.0"
MERGE_SIDE_PANEL_WIDTH = 470
GPS_MAP_MAX_DISPLAY_POINTS = 2000
WEB_MERCATOR_MAX_LAT = 85.05112878
MIN_LON = -180.0
MAX_LON = 180.0
OSM_FAILED_TILES: dict[tuple[int, int, int], float] = {}
OsmTileKey = tuple[int, int, int]
OsmTileLayer = tuple[np.ndarray, tuple[float, float, float, float]]
THEME_COLORS = {
    "light": {
        "bg": "#f8fafc",
        "panel": "#ffffff",
        "fg": "#0f172a",
        "muted": "#475569",
        "field": "#ffffff",
        "border": "#cbd5e1",
        "button": "#e2e8f0",
        "button_active": "#cbd5e1",
        "select": "#dbeafe",
        "select_fg": "#0f172a",
        "chart_bg": "#ffffff",
        "chart_axis": "#ffffff",
        "grid": "#cbd5e1",
    },
    "dark": {
        "bg": "#111827",
        "panel": "#1f2937",
        "fg": "#f8fafc",
        "muted": "#cbd5e1",
        "field": "#0f172a",
        "border": "#475569",
        "button": "#374151",
        "button_active": "#4b5563",
        "select": "#1d4ed8",
        "select_fg": "#ffffff",
        "chart_bg": "#111827",
        "chart_axis": "#1f2937",
        "grid": "#475569",
    },
}


@dataclass(frozen=True)
class TelemetryPacket:
    raw_line: str
    source: str
    arrival_time: float
    rssi: Optional[int]
    snr: Optional[float]
    millis: int
    lat: float
    lon: float
    alt_gps: float
    sats: int
    alt_baro: float
    temp: float
    pressure: float
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    qw: float
    qx: float
    qy: float
    qz: float
    high_ax: float
    high_ay: float
    high_az: float
    voltage: float
    current: float
    watt: float

    @property
    def gps_valid(self) -> bool:
        return (
            self.sats > 0
            and math.isfinite(self.lat)
            and math.isfinite(self.lon)
            and -WEB_MERCATOR_MAX_LAT <= self.lat <= WEB_MERCATOR_MAX_LAT
            and MIN_LON <= self.lon <= MAX_LON
            and not (self.lat == 0.0 and self.lon == 0.0)
        )

    @property
    def accel_mag(self) -> float:
        return math.sqrt(self.ax * self.ax + self.ay * self.ay + self.az * self.az)

    def csv_values(self) -> list[str]:
        return [
            str(self.millis),
            f"{self.lat:.6f}",
            f"{self.lon:.6f}",
            f"{self.alt_gps:.2f}",
            str(self.sats),
            f"{self.alt_baro:.2f}",
            f"{self.temp:.2f}",
            f"{self.pressure:.2f}",
            f"{self.ax:.4f}",
            f"{self.ay:.4f}",
            f"{self.az:.4f}",
            f"{self.gx:.4f}",
            f"{self.gy:.4f}",
            f"{self.gz:.4f}",
            f"{self.qw:.4f}",
            f"{self.qx:.4f}",
            f"{self.qy:.4f}",
            f"{self.qz:.4f}",
            f"{self.high_ax:.2f}",
            f"{self.high_ay:.2f}",
            f"{self.high_az:.2f}",
            f"{self.voltage:.3f}",
            f"{self.current:.3f}",
            f"{self.watt:.3f}",
        ]


LOW_VOLTAGE_THRESHOLD = 3.5
WEAK_RSSI_THRESHOLD = -110
STALE_PACKET_SECONDS = 3.0
MALFORMED_BURST_THRESHOLD = 5


@dataclass
class PortState:
    source: str
    status: str = "offline"
    message: str = ""
    latest_raw_line: str = ""
    latest_packet: Optional[TelemetryPacket] = None
    latest_rssi: Optional[int] = None
    latest_snr: Optional[float] = None
    packet_count: int = 0
    malformed_count: int = 0
    last_seen_time: Optional[float] = None
    recent_malformed: int = 0

    def record_status(self, status: str, message: str = "") -> None:
        self.status = status
        self.message = message

    def record_raw(self, line: str, arrival_time: float) -> None:
        self.latest_raw_line = line
        self.last_seen_time = arrival_time

    def record_link(self, rssi: Optional[int], snr: Optional[float]) -> None:
        if rssi is not None:
            self.latest_rssi = rssi
        if snr is not None:
            self.latest_snr = snr

    def record_packet(self, packet: TelemetryPacket) -> None:
        self.latest_packet = packet
        self.packet_count += 1
        self.recent_malformed = 0
        self.last_seen_time = packet.arrival_time

    def record_malformed(self, line: str, arrival_time: Optional[float] = None) -> None:
        self.latest_raw_line = line
        self.malformed_count += 1
        self.recent_malformed += 1
        if arrival_time is not None:
            self.last_seen_time = arrival_time


def evaluate_alerts(packet: Optional[TelemetryPacket], now: Optional[float] = None) -> set[str]:
    if packet is None:
        return {"no_packet"}

    current_time = time.time() if now is None else now
    alerts: set[str] = set()
    if packet.voltage < LOW_VOLTAGE_THRESHOLD:
        alerts.add("low_voltage")
    if packet.rssi is not None and packet.rssi < WEAK_RSSI_THRESHOLD:
        alerts.add("weak_rssi")
    if not packet.gps_valid:
        alerts.add("no_gps_lock")
    if current_time - packet.arrival_time > STALE_PACKET_SECONDS:
        alerts.add("stale_packet")
    return alerts


def evaluate_port_alerts(state: PortState, now: Optional[float] = None) -> set[str]:
    alerts = evaluate_alerts(state.latest_packet, now)
    if state.recent_malformed >= MALFORMED_BURST_THRESHOLD:
        alerts.add("malformed_burst")
    return alerts


class TelemetryParser:
    LINK_RE = re.compile(r"RSSI=(-?\d+)\s+SNR=(-?\d+(?:\.\d+)?)")
    LEADING_INT_RE = re.compile(r"\s*(\d+)")

    @staticmethod
    def parse_link_comment(line: str) -> tuple[Optional[int], Optional[float]]:
        match = TelemetryParser.LINK_RE.search(line)
        if not match:
            return None, None
        return int(match.group(1)), float(match.group(2))

    @staticmethod
    def parse_packet(
        line: str,
        source: str,
        rssi: Optional[int],
        snr: Optional[float],
        arrival_time: Optional[float] = None,
    ) -> TelemetryPacket:
        parts = TelemetryParser._normalise_fields(line)
        millis = TelemetryParser._parse_millis(parts[0])
        voltage = TelemetryParser._float_or_nan(parts[21])
        current = TelemetryParser._float_or_nan(parts[22])
        watt = TelemetryParser._parse_watt(parts[23], voltage, current)
        values = {
            "millis": millis,
            "lat": TelemetryParser._float_or_nan(parts[1]),
            "lon": TelemetryParser._float_or_nan(parts[2]),
            "alt_gps": TelemetryParser._float_or_nan(parts[3]),
            "sats": TelemetryParser._int_or_nan(parts[4]),
            "alt_baro": TelemetryParser._float_or_nan(parts[5]),
            "temp": TelemetryParser._float_or_nan(parts[6]),
            "pressure": TelemetryParser._float_or_nan(parts[7]),
            "ax": TelemetryParser._float_or_nan(parts[8]),
            "ay": TelemetryParser._float_or_nan(parts[9]),
            "az": TelemetryParser._float_or_nan(parts[10]),
            "gx": TelemetryParser._float_or_nan(parts[11]),
            "gy": TelemetryParser._float_or_nan(parts[12]),
            "gz": TelemetryParser._float_or_nan(parts[13]),
            "qw": TelemetryParser._float_or_nan(parts[14]),
            "qx": TelemetryParser._float_or_nan(parts[15]),
            "qy": TelemetryParser._float_or_nan(parts[16]),
            "qz": TelemetryParser._float_or_nan(parts[17]),
            "high_ax": TelemetryParser._float_or_nan(parts[18]),
            "high_ay": TelemetryParser._float_or_nan(parts[19]),
            "high_az": TelemetryParser._float_or_nan(parts[20]),
            "voltage": voltage,
            "current": current,
            "watt": watt,
        }

        return TelemetryPacket(
            raw_line=line,
            source=source,
            arrival_time=time.time() if arrival_time is None else arrival_time,
            rssi=rssi,
            snr=snr,
            **values,
        )

    @staticmethod
    def _normalise_fields(line: str) -> list[str]:
        data_part = line.split("#", 1)[0].strip()
        if not data_part:
            raise ValueError("empty telemetry row")
        parts = [part.strip() for part in data_part.split(",")]
        if len(parts) < CSV_FIELD_COUNT:
            parts.extend([""] * (CSV_FIELD_COUNT - len(parts)))
        return parts[:CSV_FIELD_COUNT]

    @staticmethod
    def _parse_millis(value: str) -> int:
        try:
            return int(float(value))
        except ValueError as exc:
            match = TelemetryParser.LEADING_INT_RE.match(value)
            if match:
                return int(match.group(1))
            raise ValueError(f"invalid millis field: {value!r}") from exc

    @staticmethod
    def _float_or_nan(value: str) -> float:
        if value == "":
            return math.nan
        try:
            return float(value)
        except ValueError:
            return math.nan

    @staticmethod
    def _int_or_nan(value: str):
        if value == "":
            return math.nan
        try:
            return int(float(value))
        except ValueError:
            return math.nan

    @staticmethod
    def _parse_watt(value: str, voltage: float, current: float) -> float:
        if value:
            return TelemetryParser._float_or_nan(value)
        if math.isfinite(voltage) and math.isfinite(current):
            return voltage * current / 1000.0
        return math.nan


class MergeBuffer:
    def __init__(self, max_packets: int = 2000):
        self.max_packets = max_packets
        self.selected: dict[int, TelemetryPacket] = {}
        self.history: list[int] = []

    def add(self, packet: TelemetryPacket) -> TelemetryPacket:
        current = self.selected.get(packet.millis)
        if current is None:
            self.selected[packet.millis] = packet
            self.history.append(packet.millis)
            self._trim()
            return packet

        if self._is_better(packet, current):
            self.selected[packet.millis] = packet
            return packet

        return current

    @staticmethod
    def _is_better(candidate: TelemetryPacket, current: TelemetryPacket) -> bool:
        if candidate.rssi is not None and current.rssi is not None:
            return candidate.rssi > current.rssi
        if candidate.rssi is not None and current.rssi is None:
            return True
        if candidate.rssi is None and current.rssi is not None:
            return False
        return candidate.arrival_time < current.arrival_time

    def _trim(self) -> None:
        while len(self.history) > self.max_packets:
            oldest = self.history.pop(0)
            self.selected.pop(oldest, None)


class LogWriter:
    def __init__(self, log_dir: Path = LOG_DIR, session_id: Optional[str] = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.started = datetime.now().isoformat(timespec="seconds")
        self.files = {
            "port1": self._open("port1"),
            "port2": self._open("port2"),
            "merged": self._open("merged"),
            "events": self._open("events"),
        }
        self._write_headers()

    def _open(self, suffix: str):
        return open(self.log_dir / f"{self.session_id}_{suffix}.csv", "a", buffering=1, newline="")

    def _path(self, suffix: str) -> Path:
        return self.log_dir / f"{self.session_id}_{suffix}.csv"

    def _write_headers(self) -> None:
        for source in ("port1", "port2"):
            self.files[source].write(f"# session start {self.started}\n")
            self.files[source].write(f"{RAW_LOG_HEADER}\n")
        self._write_merged_header(self.files["merged"])
        self.files["events"].write(f"# session start {self.started}\n")
        self.files["events"].write("timestamp,event,note\n")

    def _write_merged_header(self, file_obj) -> None:
        file_obj.write(f"# session start {self.started}\n")
        file_obj.write(f"{MERGED_LOG_HEADER}\n")

    def write_raw(self, source: str, line: str, arrival_time: Optional[float] = None) -> None:
        if source not in ("port1", "port2"):
            raise ValueError(f"invalid raw log source: {source}")
        csv.writer(self.files[source]).writerow([self._format_log_time(arrival_time), line.rstrip("\r\n")])

    def write_merged(self, packet: TelemetryPacket) -> None:
        csv.writer(self.files["merged"]).writerow(self._merged_row(packet))

    def rewrite_merged(self, packets: Iterable[TelemetryPacket]) -> None:
        merged_path = self._path("merged")
        tmp_path = merged_path.with_suffix(merged_path.suffix + ".tmp")
        with open(tmp_path, "w", newline="") as file_obj:
            self._write_merged_header(file_obj)
            writer = csv.writer(file_obj)
            for packet in packets:
                writer.writerow(self._merged_row(packet))
        self.files["merged"].close()
        try:
            tmp_path.replace(merged_path)
        finally:
            self.files["merged"] = self._open("merged")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def write_event(self, event: str, note: str = "") -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        safe_event = event.replace("\n", " ").replace("\r", " ")
        safe_note = note.replace("\n", " ").replace("\r", " ")
        csv.writer(self.files["events"]).writerow([timestamp, safe_event, safe_note])

    def _merged_row(self, packet: TelemetryPacket) -> list[str]:
        rssi = "" if packet.rssi is None else str(packet.rssi)
        snr = "" if packet.snr is None else f"{packet.snr:.2f}"
        return [self._format_log_time(packet.arrival_time)] + packet.csv_values() + [packet.source, rssi, snr]

    def _format_log_time(self, timestamp: Optional[float] = None) -> str:
        if timestamp is None:
            return datetime.now().isoformat(timespec="seconds")
        return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")

    def close(self) -> None:
        for file_obj in self.files.values():
            file_obj.close()


class ReplayReader:
    def __init__(
        self,
        path: Path,
        source: str,
        event_queue: queue.Queue,
        speed: float = 1.0,
        sleep_func: Callable[[float], None] = time.sleep,
    ):
        self.path = Path(path)
        self.source = source
        self.event_queue = event_queue
        self.speed = max(speed, 0.1)
        self.sleep_func = sleep_func
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name=f"ReplayReader-{self.source}", daemon=True)
        thread.start()
        return thread

    def run(self) -> None:
        total_lines = self._count_replay_lines()
        self.event_queue.put({
            "type": "status",
            "source": self.source,
            "status": "replay",
            "message": f"0/{total_lines} lines",
        })
        emitted = 0
        emitted_data_rows = 0
        for line in self._iter_lines():
            if self.stop_event.is_set():
                break
            while self.pause_event.is_set() and not self.stop_event.is_set():
                self.sleep_func(0.05)
            if self.stop_event.is_set():
                break
            is_comment = line.startswith("#")
            if emitted_data_rows > 0 and not is_comment:
                self.sleep_func(self._line_delay())
                if self.stop_event.is_set():
                    break
                while self.pause_event.is_set() and not self.stop_event.is_set():
                    self.sleep_func(0.05)
                if self.stop_event.is_set():
                    break
            self.event_queue.put({
                "type": "line",
                "source": self.source,
                "line": line,
                "arrival_time": time.time(),
                "mode": "replay",
            })
            emitted += 1
            if not is_comment:
                emitted_data_rows += 1
            if emitted == total_lines or emitted % 100 == 0:
                self.event_queue.put({
                    "type": "status",
                    "source": self.source,
                    "status": "replay",
                    "message": f"{emitted}/{total_lines} lines",
                })
        self.event_queue.put({
            "type": "status",
            "source": self.source,
            "status": "replay_done",
            "message": f"{emitted}/{total_lines} lines",
        })

    def run_once_for_test(self) -> None:
        for line in self._iter_lines():
            self.event_queue.put({
                "type": "line",
                "source": self.source,
                "line": line,
                "arrival_time": time.time(),
                "mode": "replay",
            })

    def _iter_lines(self) -> Iterable[str]:
        with open(self.path, "r", encoding="utf-8", errors="replace") as file_obj:
            for raw in file_obj:
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                replay_line = self._normalise_replay_line(line)
                if replay_line:
                    yield replay_line

    def _normalise_replay_line(self, line: str) -> Optional[str]:
        if line.startswith("#"):
            if line.startswith("# session start") or line.startswith(f"# {CSV_HEADER}"):
                return None
            return line
        try:
            row = next(csv.reader([line]))
        except csv.Error:
            return line
        if not row:
            return None
        if row[0] == LOG_TIME_FIELD:
            return None
        if len(row) == 2 and self._looks_like_log_time(row[0]):
            return row[1]
        if len(row) >= CSV_FIELD_COUNT + 1 and self._looks_like_log_time(row[0]):
            return ",".join(row[1 : 1 + CSV_FIELD_COUNT])
        return line

    def _count_replay_lines(self) -> int:
        return sum(1 for _line in self._iter_lines())

    def _looks_like_log_time(self, value: str) -> bool:
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
        return True

    def _line_delay(self) -> float:
        return min(0.5, max(0.005, 0.05 / self.speed))

    def stop(self) -> None:
        self.stop_event.set()

    def pause(self) -> None:
        self.pause_event.set()

    def resume(self) -> None:
        self.pause_event.clear()


def default_serial_factory(port: str, baud: int, timeout: float):
    import serial

    return serial.Serial(port, baud, timeout=timeout)


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    return [port.device for port in sorted(list_ports.comports())]


def configure_window_icon(root: tk.Tk, icon_path: Path = APP_ICON_PATH) -> bool:
    if not icon_path.exists():
        return False
    try:
        icon_image = tk.PhotoImage(file=str(icon_path))
        root.iconphoto(True, icon_image)
    except (OSError, tk.TclError):
        return False
    root._app_icon_image = icon_image
    return True


def valid_gps_packets(packets: Iterable[TelemetryPacket]) -> list[TelemetryPacket]:
    return [packet for packet in packets if packet.gps_valid]


def gps_map_display_packets(
    packets: list[TelemetryPacket],
    max_points: int = GPS_MAP_MAX_DISPLAY_POINTS,
) -> list[TelemetryPacket]:
    if len(packets) <= max_points:
        return packets
    if max_points < 2:
        return packets[-max_points:] if max_points > 0 else []
    last_index = len(packets) - 1
    step = last_index / (max_points - 1)
    return [packets[int(index * step)] for index in range(max_points - 1)] + [packets[-1]]


def osm_tile_xy(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    lat = max(min(lat, WEB_MERCATOR_MAX_LAT), -WEB_MERCATOR_MAX_LAT)
    lat_rad = math.radians(lat)
    scale = 2 ** zoom
    x = (lon + 180.0) / 360.0 * scale
    y = (1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi) / 2.0 * scale
    return x, y


def osm_tile_bounds(x: int, y: int, zoom: int) -> tuple[float, float, float, float]:
    scale = 2 ** zoom
    lon_left = x / scale * 360.0 - 180.0
    lon_right = (x + 1) / scale * 360.0 - 180.0
    lat_top = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / scale))))
    lat_bottom = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * (y + 1) / scale))))
    return lon_left, lon_right, lat_bottom, lat_top


def choose_osm_zoom(
    lons: list[float],
    lats: list[float],
    max_tiles: int = OSM_MAX_TILES_PER_REFRESH,
    max_zoom: int = 18,
    min_zoom: int = 1,
) -> int:
    for zoom in range(max_zoom, min_zoom - 1, -1):
        tiles = osm_tile_range(lons, lats, zoom)
        if len(tiles) <= max_tiles:
            return zoom
    return min_zoom


def osm_tiles_for_points(lons: list[float], lats: list[float]) -> list[OsmTileKey]:
    if not lons or not lats:
        return []
    zoom = choose_osm_zoom(lons, lats)
    return osm_tile_range(lons, lats, zoom)[:OSM_MAX_TILES_PER_REFRESH]


def osm_tile_range(lons: list[float], lats: list[float], zoom: int) -> list[tuple[int, int, int]]:
    min_x, min_y = osm_tile_xy(max(lats), min(lons), zoom)
    max_x, max_y = osm_tile_xy(min(lats), max(lons), zoom)
    scale = (2 ** zoom) - 1
    x0 = max(0, min(scale, int(math.floor(min_x))))
    x1 = max(0, min(scale, int(math.floor(max_x))))
    y0 = max(0, min(scale, int(math.floor(min_y))))
    y1 = max(0, min(scale, int(math.floor(max_y))))
    return [(zoom, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def load_osm_tile(z: int, x: int, y: int, cache_dir: Path = OSM_TILE_CACHE_DIR) -> Optional[np.ndarray]:
    cache_path = cache_dir / str(z) / str(x) / f"{y}.png"
    tile_key = (z, x, y)
    retry_at = OSM_FAILED_TILES.get(tile_key)
    if retry_at is not None and time.time() < retry_at and not cache_path.exists():
        return None
    try:
        if not cache_path.exists():
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            request = urllib.request.Request(
                f"https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                headers={"User-Agent": OSM_TILE_USER_AGENT},
            )
            with urllib.request.urlopen(request, timeout=OSM_TILE_TIMEOUT_SECONDS) as response:
                cache_path.write_bytes(response.read())
        from PIL import Image

        with Image.open(cache_path) as image:
            return np.asarray(image.convert("RGB"))
    except Exception:
        OSM_FAILED_TILES[tile_key] = time.time() + OSM_TILE_RETRY_SECONDS
        return None


def osm_tile_layers(
    lons: list[float],
    lats: list[float],
    cache_dir: Path = OSM_TILE_CACHE_DIR,
) -> list[OsmTileLayer]:
    return osm_layers_for_tiles(osm_tiles_for_points(lons, lats), cache_dir)


def osm_layers_for_tiles(
    tiles: Iterable[OsmTileKey],
    cache_dir: Path = OSM_TILE_CACHE_DIR,
) -> list[OsmTileLayer]:
    layers = []
    for z, x, y in tiles:
        image = load_osm_tile(z, x, y, cache_dir)
        if image is not None:
            layers.append((image, osm_tile_bounds(x, y, z)))
    return layers


class SerialReader:
    def __init__(
        self,
        source: str,
        port: str,
        baud: int,
        event_queue: queue.Queue,
        serial_factory: Callable = default_serial_factory,
        reconnect_delay: float = 1.0,
    ):
        self.source = source
        self.port = port
        self.baud = baud
        self.event_queue = event_queue
        self.serial_factory = serial_factory
        self.reconnect_delay = reconnect_delay
        self.stop_event = threading.Event()
        self.serial_obj = None

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name=f"SerialReader-{self.source}", daemon=True)
        thread.start()
        return thread

    def run(self) -> None:
        while not self.stop_event.is_set():
            if not self._connect():
                if self.stop_event.wait(self.reconnect_delay):
                    break
                continue
            self._read_loop()

    def _connect(self) -> bool:
        try:
            self.serial_obj = self.serial_factory(self.port, self.baud, timeout=1)
        except Exception as exc:
            if self.stop_event.is_set():
                return False
            self.event_queue.put({
                "type": "status",
                "source": self.source,
                "status": "reconnecting",
                "message": str(exc),
            })
            return False
        if self.stop_event.is_set():
            self._close_serial()
            return False
        self.event_queue.put({"type": "status", "source": self.source, "status": "connected"})
        return True

    def _read_loop(self) -> None:
        assert self.serial_obj is not None
        try:
            while not self.stop_event.is_set():
                raw = self.serial_obj.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    self.event_queue.put({
                        "type": "line",
                        "source": self.source,
                        "line": line,
                        "arrival_time": time.time(),
                        "mode": "serial",
                    })
        except Exception as exc:
            if not self.stop_event.is_set():
                self.event_queue.put({
                    "type": "status",
                    "source": self.source,
                    "status": "reconnecting",
                    "message": str(exc),
                })
        finally:
            self._close_serial()

    def _close_serial(self) -> None:
        serial_obj = self.serial_obj
        if serial_obj is not None:
            self.serial_obj = None
            try:
                serial_obj.close()
            except Exception:
                pass

    def stop(self) -> None:
        self.stop_event.set()
        self._close_serial()

    def run_once_for_test(self) -> None:
        if self._connect():
            try:
                assert self.serial_obj is not None
                raw = self.serial_obj.readline()
                if raw:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line:
                        self.event_queue.put({
                            "type": "line",
                            "source": self.source,
                            "line": line,
                            "arrival_time": time.time(),
                            "mode": "serial",
                        })
            finally:
                self._close_serial()

    def try_connect_once_for_test(self) -> None:
        self._connect()
        self._close_serial()


class SerialEventQueue:
    def __init__(self, event_queue: queue.Queue, generation: int):
        self.event_queue = event_queue
        self.generation = generation

    def put(self, event: dict, *args, **kwargs) -> None:
        tagged_event = dict(event)
        tagged_event["generation"] = self.generation
        tagged_event.setdefault("mode", "serial")
        self.event_queue.put(tagged_event, *args, **kwargs)


class GroundStationMonitorApp(tk.Tk):
    def __init__(self, use_interactive_map: Optional[bool] = None):
        super().__init__()
        self.title("Duck2Dragon Monitor")
        configure_window_icon(self)
        self.geometry("1280x820")
        self.use_interactive_gps_map = (
            tkintermapview is not None if use_interactive_map is None else bool(use_interactive_map and tkintermapview)
        )
        self.theme_name = "light"
        self.theme_button_var = tk.StringVar(value="Dark Mode")
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.event_queue: queue.Queue = queue.Queue()
        self.port_states = {"port1": PortState("port1"), "port2": PortState("port2")}
        self.merge_buffer = MergeBuffer()
        self.log_writer: Optional[LogWriter] = None
        self.readers: dict[str, SerialReader] = {}
        self.reader_threads: dict[str, threading.Thread] = {}
        self.replay_readers: list[ReplayReader] = []
        self.replay_threads: list[threading.Thread] = []
        self.replay_paused = False
        self.reader_generations = {"port1": 0, "port2": 0}
        self.session_started = time.time()
        self.merged_count = 0
        self.event_markers: list[tuple[str, float]] = []
        self.merged_log_packets: list[TelemetryPacket] = []
        self.merged_packets: list[TelemetryPacket] = []
        self.port_packets = {"port1": [], "port2": []}
        self.dirty_merge_charts = False
        self.dirty_port_charts = {"port1": False, "port2": False}
        self.last_chart_refresh = 0.0
        self.ui_error_count = 0
        self.osm_layer_cache: dict[tuple[OsmTileKey, ...], list[OsmTileLayer]] = {}
        self.osm_tile_requests: set[tuple[OsmTileKey, ...]] = set()
        self.gps_start_marker: Any = None
        self.gps_current_marker: Any = None
        self.gps_track_path: Any = None
        self.gps_point_markers: list[Any] = []
        self.gps_point_icon = self._make_gps_point_icon()
        self.gps_map_centered = False
        self._build_ui()
        self._apply_theme(redraw=False)
        self.after(100, self._drain_events)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_controls()
        self._build_tabs()

    def _build_controls(self) -> None:
        frame = ttk.Frame(self, padding=8)
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)

        self.port_vars = {
            "port1": tk.StringVar(value=DEFAULT_PORT_1),
            "port2": tk.StringVar(value=DEFAULT_PORT_2),
        }
        self.baud_vars = {
            "port1": tk.StringVar(value=str(DEFAULT_BAUD)),
            "port2": tk.StringVar(value=str(DEFAULT_BAUD)),
        }
        self.status_vars = {
            "port1": tk.StringVar(value="offline"),
            "port2": tk.StringVar(value="offline"),
        }
        self.summary_var = tk.StringVar(value="Idle")
        self.replay_speed_var = tk.StringVar(value="1.0")
        self.replay_pause_var = tk.StringVar(value="Pause Replay")

        port1_row = ttk.Frame(frame)
        port1_row.grid(row=0, column=0, sticky="", pady=(0, 4))
        ttk.Label(port1_row, text="Port 1").pack(side="left", padx=4)
        self.port1_combo = ttk.Combobox(port1_row, textvariable=self.port_vars["port1"], width=18)
        self.port1_combo.pack(side="left", padx=4)
        ttk.Entry(port1_row, textvariable=self.baud_vars["port1"], width=8).pack(side="left", padx=4)
        ttk.Button(port1_row, text="Connect 1", command=lambda: self._connect_port("port1")).pack(side="left", padx=4)
        ttk.Label(port1_row, textvariable=self.status_vars["port1"]).pack(side="left", padx=4)
        self.disconnect1_button = ttk.Button(
            port1_row,
            text="Disconnect 1",
            command=lambda: self._disconnect_port("port1"),
        )
        self.disconnect1_button.pack(side="left", padx=2)

        port2_row = ttk.Frame(frame)
        port2_row.grid(row=1, column=0, sticky="", pady=(0, 4))
        ttk.Label(port2_row, text="Port 2").pack(side="left", padx=4)
        self.port2_combo = ttk.Combobox(port2_row, textvariable=self.port_vars["port2"], width=18)
        self.port2_combo.pack(side="left", padx=4)
        ttk.Entry(port2_row, textvariable=self.baud_vars["port2"], width=8).pack(side="left", padx=4)
        ttk.Button(port2_row, text="Connect 2", command=lambda: self._connect_port("port2")).pack(side="left", padx=4)
        ttk.Label(port2_row, textvariable=self.status_vars["port2"]).pack(side="left", padx=4)
        self.disconnect2_button = ttk.Button(
            port2_row,
            text="Disconnect 2",
            command=lambda: self._disconnect_port("port2"),
        )
        self.disconnect2_button.pack(side="left", padx=2)

        action_row = ttk.Frame(frame)
        action_row.grid(row=2, column=0, sticky="", pady=(2, 0))
        ttk.Button(action_row, text="Refresh Ports", command=self._refresh_ports).pack(side="left", padx=4)
        ttk.Button(action_row, text="Start Logging", command=self._start_logging).pack(side="left", padx=4)
        self.stop_logging_button = ttk.Button(action_row, text="Stop Logging", command=self._stop_logging)
        self.stop_logging_button.pack(side="left", padx=2)
        ttk.Button(action_row, text="Replay Log", command=self._choose_replay_file).pack(side="left", padx=4)
        ttk.Label(action_row, text="Replay x").pack(side="left", padx=2)
        ttk.Entry(action_row, textvariable=self.replay_speed_var, width=5).pack(side="left", padx=2)
        self.replay_pause_button = ttk.Button(
            action_row,
            textvariable=self.replay_pause_var,
            command=self._toggle_replay_pause,
        )
        self.replay_pause_button.pack(side="left", padx=2)
        self.replay_stop_button = ttk.Button(action_row, text="Stop Replay", command=self._stop_replay)
        self.replay_stop_button.pack(side="left", padx=2)
        self.theme_button = ttk.Button(action_row, textvariable=self.theme_button_var, command=self._toggle_theme)
        self.theme_button.pack(side="left", padx=2)

        ttk.Label(frame, textvariable=self.summary_var).grid(row=3, column=0, sticky="", pady=(4, 0))
        self._refresh_ports()

    def _build_tabs(self) -> None:
        self.tabs = ttk.Notebook(self)
        self.tabs.grid(row=1, column=0, sticky="nsew")
        self.merge_tab = ttk.Frame(self.tabs, padding=8)
        self.port_tabs = {
            "port1": ttk.Frame(self.tabs, padding=8),
            "port2": ttk.Frame(self.tabs, padding=8),
        }
        self.tabs.add(self.merge_tab, text="Merge Data")
        self.tabs.add(self.port_tabs["port1"], text="Port 1")
        self.tabs.add(self.port_tabs["port2"], text="Port 2")
        self._build_merge_tab()
        self._build_port_tab("port1")
        self._build_port_tab("port2")

    def _build_merge_tab(self) -> None:
        self.merge_tab.columnconfigure(0, weight=1)
        self.merge_tab.columnconfigure(1, weight=0, minsize=MERGE_SIDE_PANEL_WIDTH)
        self.merge_tab.rowconfigure(1, weight=1)
        self.merge_status_var = tk.StringVar(value="No merged packets")
        ttk.Label(self.merge_tab, textvariable=self.merge_status_var, font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8)
        )
        chart_area = ttk.Frame(self.merge_tab)
        chart_area.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        chart_area.columnconfigure(0, weight=1)
        chart_area.rowconfigure(0, weight=2)
        chart_area.rowconfigure(1, weight=1)

        gps_frame = ttk.LabelFrame(chart_area, text="GPS Track Map")
        gps_frame.grid(row=0, column=0, sticky="nsew")
        gps_controls = ttk.Frame(gps_frame)
        gps_controls.pack(fill="x", padx=4, pady=(4, 0))
        self.gps_map_status_var = tk.StringVar(value="Map: waiting for GPS fix")
        ttk.Label(gps_controls, textvariable=self.gps_map_status_var).pack(side="left")
        if self.use_interactive_gps_map:
            self.gps_map_widget = tkintermapview.TkinterMapView(gps_frame, corner_radius=0)
            self.gps_map_widget.pack(fill="both", expand=True, padx=4, pady=4)
            self.gps_map_widget.set_zoom(16)
        else:
            self.gps_map_widget = None
            self.gps_fig, self.gps_ax, self.gps_canvas = self._make_figure(gps_frame, "GPS Track", "relative north")
            if tkintermapview is None:
                self.gps_map_status_var.set("Map: install tkintermapview for draggable live map")

        lower_charts = ttk.Frame(chart_area)
        lower_charts.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        lower_charts.columnconfigure(0, weight=1)
        lower_charts.columnconfigure(1, weight=1)
        alt_frame = ttk.LabelFrame(lower_charts, text="Altitude")
        alt_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        link_frame = ttk.LabelFrame(lower_charts, text="Link Quality")
        link_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self.alt_fig, self.alt_ax, self.alt_canvas = self._make_figure(alt_frame, "Altitude", "m")
        self.link_fig, self.link_ax, self.link_canvas = self._make_figure(link_frame, "RSSI/SNR", "dBm / dB")
        side = ttk.Frame(self.merge_tab, width=MERGE_SIDE_PANEL_WIDTH)
        side.grid(row=1, column=1, sticky="nsew")
        side.grid_propagate(False)
        self.merge_side_panel = side
        self.readout_var = tk.StringVar(value="Lat/Lon: --\nSats: --\nVoltage: --\nCurrent: --\nWatt: --\nRSSI: --")
        ttk.Label(side, textvariable=self.readout_var, justify="left").pack(anchor="w")
        event_frame = ttk.LabelFrame(side, text="Timeline Events")
        event_frame.pack(fill="x", pady=8)
        event_buttons = ttk.Frame(event_frame)
        event_buttons.pack(fill="x")
        for label in ("Launch", "Apogee", "Deployment", "Landing"):
            ttk.Button(event_buttons, text=label, command=lambda name=label: self._record_event(name)).pack(
                side="left", padx=2, pady=4
            )
        self.timeline_tree = self._make_tree(event_frame, ("time", "event"), height=4)
        self.timeline_tree.pack(fill="x", pady=(0, 4))
        self.merged_tree = self._make_tree(side, ("millis", "source", "alt_baro", "voltage", "rssi"))
        self.merged_tree.pack(fill="both", expand=True, pady=8)

    def _build_port_tab(self, source: str) -> None:
        tab = self.port_tabs[source]
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        status = tk.StringVar(value="No data")
        setattr(self, f"{source}_detail_var", status)
        ttk.Label(tab, textvariable=status, justify="left").grid(row=0, column=0, sticky="ew")
        chart_box = ttk.LabelFrame(tab, text="Charts")
        chart_box.grid(row=1, column=0, sticky="ew", pady=8)
        chart_box.columnconfigure(0, weight=1)
        chart_box.columnconfigure(1, weight=1)
        chart_box.columnconfigure(2, weight=1)
        figures = {}
        for idx, (key, title, ylabel) in enumerate(
            (("altitude", "Altitude", "m"), ("voltage", "Voltage", "V"), ("rssi", "RSSI", "dBm"))
        ):
            frame = ttk.Frame(chart_box)
            frame.grid(row=0, column=idx, sticky="nsew", padx=3)
            figures[key] = self._make_figure(frame, title, ylabel)
        setattr(self, f"{source}_figures", figures)
        tree = self._make_tree(tab, ("millis", "alt_baro", "voltage", "rssi", "snr"))
        tree.grid(row=2, column=0, sticky="nsew")
        setattr(self, f"{source}_tree", tree)

    def _make_figure(self, parent, title: str, ylabel: str):
        figure = Figure(figsize=(4, 2.4), dpi=100)
        axis = figure.add_subplot(111)
        axis.set_title(title)
        axis.set_xlabel("sample")
        axis.set_ylabel(ylabel)
        axis.grid(True, alpha=0.3)
        self._style_axis(axis)
        canvas = FigureCanvasTkAgg(figure, master=parent)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        return figure, axis, canvas

    def _make_tree(self, parent, columns: tuple[str, ...], height: int = 12) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=height)
        for column in columns:
            tree.heading(column, text=column)
            tree.column(column, width=90, anchor="center")
        return tree

    def _make_gps_point_icon(self) -> tk.PhotoImage:
        icon = tk.PhotoImage(width=7, height=7)
        rows = ((3, 3), (2, 4), (1, 5), (0, 6), (1, 5), (2, 4), (3, 3))
        for y, (x1, x2) in enumerate(rows):
            icon.put("#2563eb", to=(x1, y, x2 + 1, y + 1))
        return icon

    def _toggle_theme(self) -> None:
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.theme_button_var.set("Light Mode" if self.theme_name == "dark" else "Dark Mode")
        self._apply_theme(redraw=True)

    def _apply_theme(self, redraw: bool = True) -> None:
        colors = THEME_COLORS[self.theme_name]
        self.configure(bg=colors["bg"])
        self.style.configure(".", background=colors["bg"], foreground=colors["fg"], fieldbackground=colors["field"])
        self.style.configure("TFrame", background=colors["bg"])
        self.style.configure("TLabel", background=colors["bg"], foreground=colors["fg"])
        self.style.configure("TLabelframe", background=colors["bg"], foreground=colors["fg"], bordercolor=colors["border"])
        self.style.configure("TLabelframe.Label", background=colors["bg"], foreground=colors["fg"])
        self.style.configure("TNotebook", background=colors["bg"], borderwidth=0)
        self.style.configure("TNotebook.Tab", background=colors["button"], foreground=colors["fg"], padding=(10, 4))
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", colors["panel"]), ("active", colors["button_active"])],
            foreground=[("selected", colors["fg"]), ("active", colors["fg"])],
        )
        self.style.configure("TButton", background=colors["button"], foreground=colors["fg"], bordercolor=colors["border"])
        self.style.map(
            "TButton",
            background=[("active", colors["button_active"]), ("pressed", colors["button_active"])],
            foreground=[("disabled", colors["muted"]), ("active", colors["fg"])],
        )
        self.style.configure(
            "TEntry",
            fieldbackground=colors["field"],
            foreground=colors["fg"],
            insertcolor=colors["fg"],
            bordercolor=colors["border"],
        )
        self.style.configure(
            "TCombobox",
            fieldbackground=colors["field"],
            foreground=colors["fg"],
            background=colors["field"],
            arrowcolor=colors["fg"],
            bordercolor=colors["border"],
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", colors["field"])],
            foreground=[("readonly", colors["fg"])],
            selectbackground=[("readonly", colors["select"])],
            selectforeground=[("readonly", colors["select_fg"])],
        )
        self.style.configure(
            "Treeview",
            background=colors["field"],
            fieldbackground=colors["field"],
            foreground=colors["fg"],
            bordercolor=colors["border"],
        )
        self.style.configure("Treeview.Heading", background=colors["button"], foreground=colors["fg"])
        self.style.map(
            "Treeview",
            background=[("selected", colors["select"])],
            foreground=[("selected", colors["select_fg"])],
        )
        if redraw:
            self._style_all_figures()

    def _style_all_figures(self) -> None:
        for axis, canvas in self._figure_axes():
            self._style_axis(axis)
            self._style_legend(axis)
            canvas.draw_idle()

    def _figure_axes(self) -> Iterable[tuple[Any, Any]]:
        if hasattr(self, "gps_ax"):
            yield self.gps_ax, self.gps_canvas
        if hasattr(self, "alt_ax"):
            yield self.alt_ax, self.alt_canvas
        if hasattr(self, "link_ax"):
            yield self.link_ax, self.link_canvas
        for source in ("port1", "port2"):
            figures = getattr(self, f"{source}_figures", {})
            for _figure, axis, canvas in figures.values():
                yield axis, canvas

    def _style_axis(self, axis) -> None:
        colors = THEME_COLORS[self.theme_name]
        axis.figure.set_facecolor(colors["chart_bg"])
        axis.set_facecolor(colors["chart_axis"])
        axis.title.set_color(colors["fg"])
        axis.xaxis.label.set_color(colors["fg"])
        axis.yaxis.label.set_color(colors["fg"])
        axis.tick_params(colors=colors["muted"])
        for spine in axis.spines.values():
            spine.set_color(colors["border"])
        axis.grid(True, alpha=0.35, color=colors["grid"])

    def _style_legend(self, axis) -> None:
        colors = THEME_COLORS[self.theme_name]
        legend = axis.get_legend()
        if legend is None:
            return
        legend.get_frame().set_facecolor(colors["panel"])
        legend.get_frame().set_edgecolor(colors["border"])
        for text in legend.get_texts():
            text.set_color(colors["fg"])

    def _refresh_ports(self) -> None:
        ports = list_serial_ports()
        values = ports or [DEFAULT_PORT_1, DEFAULT_PORT_2]
        self.port1_combo["values"] = values
        self.port2_combo["values"] = values

    def _start_logging(self) -> None:
        if self.log_writer is None:
            self.log_writer = LogWriter()
            self.summary_var.set(f"Logging to {self.log_writer.log_dir}")
            for source in self.port_states:
                self._update_port_view(source, refresh_charts=False)

    def _stop_logging(self) -> None:
        if self.log_writer is None:
            self.summary_var.set("Logging already stopped")
            return
        self.log_writer.close()
        self.log_writer = None
        self.summary_var.set("Logging stopped")
        for source in self.port_states:
            self._update_port_view(source, refresh_charts=False)

    def _connect_port(self, source: str) -> None:
        port = self.port_vars[source].get().strip()
        try:
            baud = int(self.baud_vars[source].get().strip())
        except ValueError:
            self.status_vars[source].set("invalid baud")
            self.summary_var.set(f"{source}: invalid baud")
            return
        self.reader_generations[source] += 1
        generation = self.reader_generations[source]
        self._stop_reader(source)
        self._start_logging()
        reader = SerialReader(source, port, baud, SerialEventQueue(self.event_queue, generation))
        self.readers[source] = reader
        self.reader_threads[source] = reader.start()

    def _disconnect_port(self, source: str) -> None:
        self.reader_generations[source] += 1
        self._stop_reader(source)
        self.port_states[source].record_status("offline", "disconnected")
        self.status_vars[source].set("offline: disconnected")
        self.summary_var.set(f"{source}: disconnected")
        self._update_port_view(source, refresh_charts=False)

    def _stop_reader(self, source: str) -> None:
        reader = self.readers.pop(source, None)
        if reader is not None:
            reader.stop()
        thread = self.reader_threads.pop(source, None)
        if thread is not None:
            self._join_reader_thread(thread)

    def _join_reader_thread(self, thread: threading.Thread) -> None:
        if thread is threading.current_thread():
            return
        is_alive = getattr(thread, "is_alive", None)
        should_join = True
        if callable(is_alive):
            should_join = is_alive()
        join = getattr(thread, "join", None)
        if should_join and callable(join):
            join(timeout=0.5)

    def _choose_replay_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose replay log",
            initialdir=str(DATA_DIR),
            filetypes=[("CSV and log files", "*.csv *.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        self._start_logging()
        try:
            speed = float(self.replay_speed_var.get())
        except ValueError:
            speed = 1.0
            self.replay_speed_var.set("1.0")
        reader = ReplayReader(Path(path), "port1", self.event_queue, speed=speed)
        self.replay_readers.append(reader)
        self.replay_threads.append(reader.start())
        self.replay_paused = False
        self.replay_pause_var.set("Pause Replay")

    def _toggle_replay_pause(self) -> None:
        if not self.replay_readers:
            self.summary_var.set("No replay active")
            return
        if self.replay_paused:
            for reader in self.replay_readers:
                reader.resume()
            self.replay_paused = False
            self.replay_pause_var.set("Pause Replay")
            self.summary_var.set("Replay resumed")
            return
        for reader in self.replay_readers:
            reader.pause()
        self.replay_paused = True
        self.replay_pause_var.set("Resume Replay")
        self.summary_var.set("Replay paused")

    def _stop_replay(self) -> None:
        if not self.replay_readers:
            self.summary_var.set("No replay active")
            return
        for reader in self.replay_readers:
            reader.stop()
        for thread in self.replay_threads:
            self._join_reader_thread(thread)
        self.replay_readers.clear()
        self.replay_threads.clear()
        self.replay_paused = False
        self.replay_pause_var.set("Pause Replay")
        self.summary_var.set("Replay stopped")

    def _record_event(self, name: str) -> None:
        timestamp = time.time()
        self.event_markers.append((name, timestamp))
        stamp = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
        self.timeline_tree.insert("", 0, values=(stamp, name))
        while len(self.timeline_tree.get_children()) > 20:
            self.timeline_tree.delete(self.timeline_tree.get_children()[-1])
        if self.log_writer is not None:
            self.log_writer.write_event(name)
        self.summary_var.set(f"Event: {name}")

    def _drain_events(self) -> None:
        processed = 0
        try:
            while processed < EVENT_DRAIN_BATCH_SIZE:
                try:
                    event = self.event_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle_event(event)
                except Exception as exc:
                    self._record_ui_error("event", exc)
                processed += 1
            try:
                self._update_summary()
            except Exception as exc:
                self._record_ui_error("summary", exc)
            try:
                self._refresh_dirty_charts()
            except Exception as exc:
                self._record_ui_error("chart", exc)
        finally:
            delay_ms = 1 if processed == EVENT_DRAIN_BATCH_SIZE and not self.event_queue.empty() else 50
            try:
                self.after(delay_ms, self._drain_events)
            except tk.TclError:
                pass

    def _record_ui_error(self, context: str, exc: Exception) -> None:
        self.ui_error_count += 1
        message = f"UI recovered from {context} error: {exc}"
        print(message)
        try:
            self.summary_var.set(message)
        except tk.TclError:
            pass

    def _handle_event(self, event: dict) -> None:
        event_type = event.get("type")
        source = event.get("source")
        if source in self.port_states and not self._is_current_serial_event(source, event):
            return
        if event_type == "status" and source in self.port_states:
            state = self.port_states[source]
            status = event.get("status", "offline")
            message = event.get("message", "")
            state.record_status(status, message)
            self.status_vars[source].set(f"{status}: {message}" if message else status)
            return
        if event_type == "line" and source in self.port_states:
            self._handle_line(source, event["line"], event.get("arrival_time", time.time()))
            return
        if event_type == "osm_tiles":
            tile_key = event.get("tile_key")
            if tile_key:
                self.osm_layer_cache[tile_key] = event.get("layers", [])
                self.osm_tile_requests.discard(tile_key)
                self._mark_merge_charts_dirty()

    def _is_current_serial_event(self, source: str, event: dict) -> bool:
        generation = event.get("generation")
        if generation is None:
            return True
        return generation == self.reader_generations[source]

    def _handle_line(self, source: str, line: str, arrival_time: float) -> None:
        state = self.port_states[source]
        state.record_raw(line, arrival_time)
        if self.log_writer is not None:
            self.log_writer.write_raw(source, line, arrival_time)
        if "#" in line:
            rssi, snr = TelemetryParser.parse_link_comment(line)
            state.record_link(rssi, snr)
        if line.startswith("#"):
            self._update_port_view(source, refresh_charts=False)
            return
        try:
            packet = TelemetryParser.parse_packet(line, source, state.latest_rssi, state.latest_snr, arrival_time)
        except ValueError:
            state.record_malformed(line, arrival_time)
            self._update_port_view(source, refresh_charts=False)
            return
        state.record_packet(packet)
        self.port_packets[source].append(packet)
        self.port_packets[source] = self.port_packets[source][-300:]
        previous_selected = self.merge_buffer.selected.get(packet.millis)
        if previous_selected is None:
            previous_selected = self._find_merged_log_packet(packet.millis)
        if previous_selected is not None:
            if MergeBuffer._is_better(packet, previous_selected):
                selected = self.merge_buffer.add(packet)
            else:
                selected = previous_selected
        else:
            selected = self.merge_buffer.add(packet)
        self._update_port_view(source, packet, refresh_charts=False)
        self._mark_port_charts_dirty(source)
        if selected is not packet:
            self._mark_merge_charts_dirty()
            return
        if previous_selected is None:
            self.merged_count += 1
            self.merged_log_packets.append(packet)
            self.merged_packets.append(packet)
            self.merged_packets = self.merged_packets[-300:]
            if self.log_writer is not None:
                self.log_writer.write_merged(packet)
            self._update_merge_view()
            return
        replaced_visible_packet = self._replace_merged_packet(packet)
        if self.log_writer is not None:
            self.log_writer.rewrite_merged(self.merged_log_packets)
        if replaced_visible_packet:
            self._update_merge_view(display_packet=packet, rebuild_tree=True)

    def _update_port_view(
        self,
        source: str,
        packet_for_row: Optional[TelemetryPacket] = None,
        now: Optional[float] = None,
        refresh_charts: bool = True,
    ) -> None:
        state = self.port_states[source]
        packet = state.latest_packet
        detail_var = getattr(self, f"{source}_detail_var")
        alerts_text = ", ".join(sorted(evaluate_port_alerts(state, now))) or "none"
        raw_line = self._display_raw_line(state.latest_raw_line)
        rssi_text = state.latest_rssi if state.latest_rssi is not None else "--"
        snr_text = f"{state.latest_snr:.2f}" if state.latest_snr is not None else "--"
        packet_snr_text = f"{packet.snr:.2f}" if packet is not None and packet.snr is not None else "--"
        log_status = self._log_status(source)
        if packet is None:
            detail_var.set(
                f"{source}: {state.status}\n"
                f"Packets: 0\n"
                f"Malformed: {state.malformed_count}\n"
                f"RSSI: {rssi_text}  SNR: {snr_text}\n"
                f"Latest raw: {raw_line}\n"
                f"{log_status}\n"
                f"Alerts: {alerts_text}"
            )
            return
        detail_var.set(
            f"{source}: {state.status}\n"
            f"Packets: {state.packet_count}  Malformed: {state.malformed_count}\n"
            f"Alt: {packet.alt_baro:.2f} m  Voltage: {packet.voltage:.3f} V  "
            f"RSSI: {packet.rssi if packet.rssi is not None else '--'}  "
            f"SNR: {packet_snr_text}\n"
            f"Latest raw: {raw_line}\n"
            f"{log_status}\n"
            f"Alerts: {alerts_text}"
        )
        if packet_for_row is not None:
            tree = getattr(self, f"{source}_tree")
            tree.insert(
                "",
                0,
                values=(
                    packet_for_row.millis,
                    f"{packet_for_row.alt_baro:.2f}",
                    f"{packet_for_row.voltage:.3f}",
                    packet_for_row.rssi,
                    packet_for_row.snr,
                ),
            )
            while len(tree.get_children()) > 200:
                tree.delete(tree.get_children()[-1])
        if refresh_charts:
            self._refresh_port_charts(source)

    def _display_raw_line(self, line: str, limit: int = 140) -> str:
        if not line:
            return "--"
        line = line.strip()
        if len(line) <= limit:
            return line
        return line[: limit - 3] + "..."

    def _log_status(self, source: str) -> str:
        if self.log_writer is None:
            return "Log: stopped"
        path_func = getattr(self.log_writer, "_path", None)
        if callable(path_func):
            return f"Log: {path_func(source)}"
        return "Log: active"

    def _replace_merged_packet(self, packet: TelemetryPacket) -> bool:
        for index, existing in enumerate(self.merged_log_packets):
            if existing.millis == packet.millis:
                self.merged_log_packets[index] = packet
                break
        for index, existing in enumerate(self.merged_packets):
            if existing.millis == packet.millis:
                self.merged_packets[index] = packet
                return True
        return False

    def _find_merged_log_packet(self, millis: int) -> Optional[TelemetryPacket]:
        for packet in self.merged_log_packets:
            if packet.millis == millis:
                return packet
        return None

    def _update_merge_view(self, display_packet: Optional[TelemetryPacket] = None, rebuild_tree: bool = False) -> None:
        if not self.merged_packets:
            return
        packet = display_packet or self.merged_packets[-1]
        alerts = evaluate_alerts(packet)
        self.merge_status_var.set(f"Merged packets: {self.merged_count}  Alerts: {', '.join(sorted(alerts)) or 'none'}")
        self.readout_var.set(
            f"Lat/Lon: {packet.lat:.6f}, {packet.lon:.6f}\n"
            f"Sats: {packet.sats}\n"
            f"Voltage: {packet.voltage:.3f} V\n"
            f"Current: {packet.current:.3f} mA\n"
            f"Watt: {packet.watt:.3f} W\n"
            f"RSSI: {packet.rssi if packet.rssi is not None else '--'}"
        )
        if rebuild_tree:
            for item in self.merged_tree.get_children():
                self.merged_tree.delete(item)
            for merged_packet in self.merged_packets[-200:]:
                self._insert_merged_row(merged_packet)
            self._mark_merge_charts_dirty()
            return
        self._insert_merged_row(packet)
        self._mark_merge_charts_dirty()

    def _insert_merged_row(self, packet: TelemetryPacket) -> None:
        self.merged_tree.insert(
            "",
            0,
            values=(packet.millis, packet.source, f"{packet.alt_baro:.2f}", f"{packet.voltage:.3f}", packet.rssi),
        )
        while len(self.merged_tree.get_children()) > 200:
            self.merged_tree.delete(self.merged_tree.get_children()[-1])

    def _refresh_merge_charts(self) -> None:
        packets = self.merged_packets[-100:]
        gps_packets = gps_map_display_packets(valid_gps_packets(self.merged_log_packets))

        if self.use_interactive_gps_map:
            self._refresh_interactive_gps_map(gps_packets)
        else:
            self._refresh_static_gps_map(gps_packets)

        self.alt_ax.clear()
        self.alt_ax.set_title("Altitude")
        self.alt_ax.grid(True, alpha=0.3)
        self.alt_ax.set_xlabel("sample")
        self.alt_ax.set_ylabel("m")
        if packets:
            self.alt_ax.plot([packet.alt_baro for packet in packets], label="baro")
            self.alt_ax.plot([packet.alt_gps for packet in packets], label="gps")
            self.alt_ax.legend(loc="upper left")
        self._style_axis(self.alt_ax)
        self._style_legend(self.alt_ax)
        self.alt_canvas.draw_idle()

        self.link_ax.clear()
        self.link_ax.set_title("RSSI/SNR")
        self.link_ax.grid(True, alpha=0.3)
        self.link_ax.set_xlabel("sample")
        self.link_ax.set_ylabel("dBm / dB")
        plotted = False
        for source, label in (("port1", "Port 1"), ("port2", "Port 2")):
            packets_for_source = self.port_packets[source][-100:]
            rssi = [packet.rssi for packet in packets_for_source if packet.rssi is not None]
            snr = [packet.snr for packet in packets_for_source if packet.snr is not None]
            if rssi:
                self.link_ax.plot(rssi, label=f"{label} RSSI")
                plotted = True
            if snr:
                self.link_ax.plot(snr, linestyle="--", label=f"{label} SNR")
                plotted = True
        if plotted:
            self.link_ax.legend(loc="lower left")
        self._style_axis(self.link_ax)
        self._style_legend(self.link_ax)
        self.link_canvas.draw_idle()

    def _refresh_static_gps_map(self, gps_packets: list[TelemetryPacket]) -> None:
        self.gps_ax.clear()
        self.gps_ax.set_title("GPS Track")
        self.gps_ax.grid(True, alpha=0.3)
        if gps_packets:
            lons = [packet.lon for packet in gps_packets]
            lats = [packet.lat for packet in gps_packets]
            tile_count, tiles_loading = self._draw_osm_tiles(lons, lats)
            self.gps_ax.scatter(lons, lats, color="#2563eb", s=14, alpha=0.8, label="gps points", zorder=3)
            self.gps_ax.scatter(lons[0], lats[0], color="#16a34a", edgecolor="white", s=46, label="start", zorder=4)
            self.gps_ax.scatter(lons[-1], lats[-1], color="#dc2626", edgecolor="white", s=52, label="current", zorder=5)
            self.gps_ax.annotate(
                f"{lats[-1]:.6f}, {lons[-1]:.6f}",
                (lons[-1], lats[-1]),
                xytext=(8, 8),
                textcoords="offset points",
                fontsize=8,
                zorder=6,
            )
            self._fit_gps_axes(lons, lats)
            self.gps_ax.set_xlabel("longitude")
            self.gps_ax.set_ylabel("latitude")
            self.gps_ax.legend(loc="best")
            loading_text = " loading" if tiles_loading else ""
            self.gps_map_status_var.set(f"Map: {len(gps_packets)} GPS points, {tile_count} OSM tiles{loading_text}")
        else:
            self.gps_ax.text(0.5, 0.5, "No GPS lock", ha="center", va="center", transform=self.gps_ax.transAxes)
            self.gps_map_status_var.set("Map: waiting for GPS fix")
        self._style_axis(self.gps_ax)
        self._style_legend(self.gps_ax)
        self.gps_canvas.draw_idle()

    def _refresh_interactive_gps_map(self, gps_packets: list[TelemetryPacket]) -> None:
        if not gps_packets:
            self.gps_map_status_var.set("Map: waiting for GPS fix")
            return
        positions = [(packet.lat, packet.lon) for packet in gps_packets]
        start_lat, start_lon = positions[0]
        current_lat, current_lon = positions[-1]
        if not self.gps_map_centered:
            self.gps_map_widget.set_position(start_lat, start_lon)
            self.gps_map_centered = True
        if self.gps_start_marker is None:
            self.gps_start_marker = self.gps_map_widget.set_marker(start_lat, start_lon, text="Start")
        if self.gps_current_marker is None:
            self.gps_current_marker = self.gps_map_widget.set_marker(current_lat, current_lon, text="Current")
        else:
            self.gps_current_marker.set_position(current_lat, current_lon)
        if self.gps_track_path is not None:
            self.gps_track_path.delete()
            self.gps_track_path = None
        self._sync_gps_point_markers(positions)
        self.gps_map_status_var.set(
            f"Map: {len(gps_packets)} GPS points, current {current_lat:.6f}, {current_lon:.6f}"
        )

    def _sync_gps_point_markers(self, positions: list[tuple[float, float]]) -> None:
        for index, (lat, lon) in enumerate(positions):
            if index < len(self.gps_point_markers):
                self.gps_point_markers[index].set_position(lat, lon)
                continue
            self.gps_point_markers.append(
                self.gps_map_widget.set_marker(lat, lon, icon=self.gps_point_icon, icon_anchor="center")
            )
        while len(self.gps_point_markers) > len(positions):
            marker = self.gps_point_markers.pop()
            marker.delete()

    def _draw_osm_tiles(self, lons: list[float], lats: list[float]) -> tuple[int, bool]:
        tile_key = tuple(osm_tiles_for_points(lons, lats))
        if not tile_key:
            return 0, False
        layers = self.osm_layer_cache.get(tile_key)
        if layers is None:
            self._request_osm_tiles(tile_key)
            layers = []
        drawn = 0
        for image, extent in layers:
            self.gps_ax.imshow(image, extent=extent, origin="upper", aspect="auto", alpha=0.9, zorder=0)
            drawn += 1
        return drawn, tile_key in self.osm_tile_requests

    def _request_osm_tiles(self, tile_key: tuple[OsmTileKey, ...]) -> None:
        if tile_key in self.osm_tile_requests:
            return
        self.osm_tile_requests.add(tile_key)

        def load_tiles() -> None:
            layers = osm_layers_for_tiles(tile_key)
            self.event_queue.put({"type": "osm_tiles", "tile_key": tile_key, "layers": layers})

        threading.Thread(target=load_tiles, daemon=True, name="osm-tile-loader").start()

    def _fit_gps_axes(self, lons: list[float], lats: list[float]) -> None:
        lon_min, lon_max = min(lons), max(lons)
        lat_min, lat_max = min(lats), max(lats)
        lon_pad = max((lon_max - lon_min) * 0.1, 0.0001)
        lat_pad = max((lat_max - lat_min) * 0.1, 0.0001)
        self.gps_ax.set_xlim(lon_min - lon_pad, lon_max + lon_pad)
        self.gps_ax.set_ylim(lat_min - lat_pad, lat_max + lat_pad)

    def _refresh_port_charts(self, source: str) -> None:
        packets = self.port_packets[source][-100:]
        figures = getattr(self, f"{source}_figures")
        series = {
            "altitude": ("Altitude", "m", [("Altitude", [packet.alt_baro for packet in packets], "-")]),
            "voltage": ("Voltage", "V", [("Voltage", [packet.voltage for packet in packets], "-")]),
            "rssi": (
                "RSSI/SNR",
                "dBm / dB",
                [
                    ("RSSI", [packet.rssi for packet in packets if packet.rssi is not None], "-"),
                    ("SNR", [packet.snr for packet in packets if packet.snr is not None], "--"),
                ],
            ),
        }
        for key, (title, ylabel, plot_series) in series.items():
            _figure, axis, canvas = figures[key]
            axis.clear()
            axis.set_title(title)
            axis.set_xlabel("sample")
            axis.set_ylabel(ylabel)
            axis.grid(True, alpha=0.3)
            plotted = False
            for label, values, style in plot_series:
                if values:
                    axis.plot(values, linestyle=style, label=label)
                    plotted = True
            if plotted and len(plot_series) > 1:
                axis.legend(loc="best")
            self._style_axis(axis)
            self._style_legend(axis)
            canvas.draw_idle()

    def _mark_merge_charts_dirty(self) -> None:
        self.dirty_merge_charts = True

    def _mark_port_charts_dirty(self, source: str) -> None:
        self.dirty_port_charts[source] = True

    def _refresh_dirty_charts(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_chart_refresh < CHART_REFRESH_INTERVAL_SECONDS:
            return
        refreshed = False
        if self.dirty_merge_charts:
            self._refresh_merge_charts()
            self.dirty_merge_charts = False
            refreshed = True
        for source, dirty in list(self.dirty_port_charts.items()):
            if dirty:
                self._refresh_port_charts(source)
                self.dirty_port_charts[source] = False
                refreshed = True
        if refreshed:
            self.last_chart_refresh = now

    def _update_summary(self, now: Optional[float] = None) -> None:
        current_time = time.time() if now is None else now
        elapsed = int(current_time - self.session_started)
        p1 = self.port_states["port1"].packet_count
        p2 = self.port_states["port2"].packet_count
        malformed = self.port_states["port1"].malformed_count + self.port_states["port2"].malformed_count
        self.summary_var.set(f"{elapsed}s  P1={p1}  P2={p2}  merged={self.merged_count}  malformed={malformed}")
        for source in self.port_states:
            self._update_port_view(source, now=current_time, refresh_charts=False)
        if self.merged_packets:
            packet = self.merged_packets[-1]
            alerts = evaluate_alerts(packet, current_time)
            self.merge_status_var.set(
                f"Merged packets: {self.merged_count}  Alerts: {', '.join(sorted(alerts)) or 'none'}"
            )

    def destroy(self) -> None:
        for source in list(self.readers):
            self._stop_reader(source)
        for reader in self.replay_readers:
            reader.stop()
        for thread in list(self.reader_threads.values()):
            self._join_reader_thread(thread)
        for thread in self.replay_threads:
            self._join_reader_thread(thread)
        if self.log_writer is not None:
            self.log_writer.close()
        super().destroy()


def main() -> int:
    app = GroundStationMonitorApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
