#!/usr/bin/env python3
"""Duck2Dragon telemetry core - packet parsing, merging, logging, alerts."""

import csv
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

# CSV format constants
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

# Coordinate bounds
WEB_MERCATOR_MAX_LAT = 85.05112878
MIN_LON = -180.0
MAX_LON = 180.0

# Alert thresholds (configurable via AlertConfig)
LOW_VOLTAGE_THRESHOLD = 3.5
WEAK_RSSI_THRESHOLD = -110
STALE_PACKET_SECONDS = 3.0
MALFORMED_BURST_THRESHOLD = 5

# Orientation constants
ORIENTATION_VERTICAL_DEGREES = 25.0
ORIENTATION_HORIZONTAL_DEGREES = 65.0
ORIENTATION_IDENTITY_EPS = 1e-4

# Directory paths
DATA_DIR = Path(__file__).resolve().parent
LOG_DIR = DATA_DIR / "logs"


@dataclass
class AlertConfig:
    """Alert configuration thresholds (placeholder - full impl in Task 10)."""
    low_voltage_threshold: float = LOW_VOLTAGE_THRESHOLD
    weak_rssi_threshold: int = WEAK_RSSI_THRESHOLD
    stale_packet_seconds: float = STALE_PACKET_SECONDS
    malformed_burst_threshold: int = MALFORMED_BURST_THRESHOLD


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
    first_packet_millis: Optional[int] = None

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
        if self.first_packet_millis is None:
            self.first_packet_millis = packet.millis
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


def evaluate_alerts(packet: Optional[TelemetryPacket], now: Optional[float] = None, config: Optional['AlertConfig'] = None) -> set[str]:
    if packet is None:
        return {"no_packet"}

    if config is None:
        config = AlertConfig()

    current_time = time.time() if now is None else now
    alerts: set[str] = set()
    if packet.voltage < config.low_voltage_threshold:
        alerts.add("low_voltage")
    if packet.rssi is not None and packet.rssi < config.weak_rssi_threshold:
        alerts.add("weak_rssi")
    if not packet.gps_valid:
        alerts.add("no_gps_lock")
    if current_time - packet.arrival_time > config.stale_packet_seconds:
        alerts.add("stale_packet")
    return alerts


def evaluate_port_alerts(state: PortState, now: Optional[float] = None, config: Optional['AlertConfig'] = None) -> set[str]:
    if config is None:
        config = AlertConfig()
    alerts = evaluate_alerts(state.latest_packet, now, config)
    if state.recent_malformed >= config.malformed_burst_threshold:
        alerts.add("malformed_burst")
    return alerts


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
