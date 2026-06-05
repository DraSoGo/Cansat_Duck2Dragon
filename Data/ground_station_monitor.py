#!/usr/bin/env python3
"""Duck2Dragon Monitor - Tkinter dual-LoRa ground station dashboard."""

import csv
import math
import queue
import re
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Iterable, Optional


CSV_HEADER = (
    "millis,lat,lon,alt_gps,sats,alt_baro,temp,pressure,"
    "ax,ay,az,gx,gy,gz,qw,qx,qy,qz,"
    "high_ax,high_ay,high_az,voltage,current,watt"
)
CSV_FIELDS = CSV_HEADER.split(",")
CSV_FIELD_COUNT = len(CSV_FIELDS)
DEFAULT_BAUD = 115200
DEFAULT_PORT_1 = "/dev/ttyACM0"
DEFAULT_PORT_2 = "/dev/ttyUSB0"
DATA_DIR = Path(__file__).resolve().parent
LOG_DIR = DATA_DIR / "logs"


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
        return self.sats > 0 and not (self.lat == 0.0 and self.lon == 0.0)

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
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != CSV_FIELD_COUNT:
            raise ValueError(f"expected 24 fields, got {len(parts)}")

        try:
            values = {
                "millis": int(parts[0]),
                "lat": float(parts[1]),
                "lon": float(parts[2]),
                "alt_gps": float(parts[3]),
                "sats": int(float(parts[4])),
                "alt_baro": float(parts[5]),
                "temp": float(parts[6]),
                "pressure": float(parts[7]),
                "ax": float(parts[8]),
                "ay": float(parts[9]),
                "az": float(parts[10]),
                "gx": float(parts[11]),
                "gy": float(parts[12]),
                "gz": float(parts[13]),
                "qw": float(parts[14]),
                "qx": float(parts[15]),
                "qy": float(parts[16]),
                "qz": float(parts[17]),
                "high_ax": float(parts[18]),
                "high_ay": float(parts[19]),
                "high_az": float(parts[20]),
                "voltage": float(parts[21]),
                "current": float(parts[22]),
                "watt": float(parts[23]),
            }
        except ValueError as exc:
            raise ValueError(f"invalid numeric field: {exc}") from exc

        return TelemetryPacket(
            raw_line=line,
            source=source,
            arrival_time=time.time() if arrival_time is None else arrival_time,
            rssi=rssi,
            snr=snr,
            **values,
        )


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
            self.files[source].write(f"# {CSV_HEADER}\n")
        self._write_merged_header(self.files["merged"])
        self.files["events"].write(f"# session start {self.started}\n")
        self.files["events"].write("timestamp,event,note\n")

    def _write_merged_header(self, file_obj) -> None:
        file_obj.write(f"# session start {self.started}\n")
        file_obj.write(f"{CSV_HEADER},source,rssi,snr\n")

    def write_raw(self, source: str, line: str) -> None:
        if source not in ("port1", "port2"):
            raise ValueError(f"invalid raw log source: {source}")
        self.files[source].write(line.rstrip("\r\n") + "\n")

    def write_merged(self, packet: TelemetryPacket) -> None:
        rssi = "" if packet.rssi is None else str(packet.rssi)
        snr = "" if packet.snr is None else f"{packet.snr:.2f}"
        row = packet.csv_values() + [packet.source, rssi, snr]
        self.files["merged"].write(",".join(row) + "\n")

    def rewrite_merged(self, packets: Iterable[TelemetryPacket]) -> None:
        merged_path = self._path("merged")
        tmp_path = merged_path.with_suffix(merged_path.suffix + ".tmp")
        with open(tmp_path, "w", newline="") as file_obj:
            self._write_merged_header(file_obj)
            for packet in packets:
                rssi = "" if packet.rssi is None else str(packet.rssi)
                snr = "" if packet.snr is None else f"{packet.snr:.2f}"
                row = packet.csv_values() + [packet.source, rssi, snr]
                file_obj.write(",".join(row) + "\n")
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
        self.event_queue.put({"type": "status", "source": self.source, "status": "replay"})
        emitted = 0
        for line in self._iter_lines():
            if self.stop_event.is_set():
                break
            while self.pause_event.is_set() and not self.stop_event.is_set():
                self.sleep_func(0.05)
            if self.stop_event.is_set():
                break
            if emitted > 0:
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
        self.event_queue.put({"type": "status", "source": self.source, "status": "replay_done"})

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
                if line:
                    yield line

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
    def __init__(self):
        super().__init__()
        self.title("Duck2Dragon Monitor")
        self.geometry("1280x820")
        self.event_queue: queue.Queue = queue.Queue()
        self.port_states = {"port1": PortState("port1"), "port2": PortState("port2")}
        self.merge_buffer = MergeBuffer()
        self.log_writer: Optional[LogWriter] = None
        self.readers: dict[str, SerialReader] = {}
        self.reader_threads: dict[str, threading.Thread] = {}
        self.replay_readers: list[ReplayReader] = []
        self.replay_threads: list[threading.Thread] = []
        self.reader_generations = {"port1": 0, "port2": 0}
        self.session_started = time.time()
        self.merged_count = 0
        self.event_markers: list[tuple[str, float]] = []
        self.merged_log_packets: list[TelemetryPacket] = []
        self.merged_packets: list[TelemetryPacket] = []
        self.port_packets = {"port1": [], "port2": []}
        self._build_ui()
        self.after(100, self._drain_events)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_controls()
        self._build_tabs()

    def _build_controls(self) -> None:
        frame = ttk.Frame(self, padding=8)
        frame.grid(row=0, column=0, sticky="ew")
        for index in range(12):
            frame.columnconfigure(index, weight=0)
        frame.columnconfigure(11, weight=1)

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

        ttk.Label(frame, text="Port 1").grid(row=0, column=0, padx=4)
        self.port1_combo = ttk.Combobox(frame, textvariable=self.port_vars["port1"], width=18)
        self.port1_combo.grid(row=0, column=1, padx=4)
        ttk.Entry(frame, textvariable=self.baud_vars["port1"], width=8).grid(row=0, column=2, padx=4)
        ttk.Button(frame, text="Connect 1", command=lambda: self._connect_port("port1")).grid(row=0, column=3, padx=4)
        ttk.Label(frame, textvariable=self.status_vars["port1"]).grid(row=0, column=4, padx=4)

        ttk.Label(frame, text="Port 2").grid(row=0, column=5, padx=4)
        self.port2_combo = ttk.Combobox(frame, textvariable=self.port_vars["port2"], width=18)
        self.port2_combo.grid(row=0, column=6, padx=4)
        ttk.Entry(frame, textvariable=self.baud_vars["port2"], width=8).grid(row=0, column=7, padx=4)
        ttk.Button(frame, text="Connect 2", command=lambda: self._connect_port("port2")).grid(row=0, column=8, padx=4)
        ttk.Label(frame, textvariable=self.status_vars["port2"]).grid(row=0, column=9, padx=4)

        ttk.Button(frame, text="Refresh Ports", command=self._refresh_ports).grid(row=0, column=10, padx=4)
        ttk.Button(frame, text="Start Logging", command=self._start_logging).grid(row=0, column=11, padx=4, sticky="w")
        ttk.Button(frame, text="Replay Log", command=self._choose_replay_file).grid(row=0, column=12, padx=4)
        ttk.Label(frame, textvariable=self.summary_var).grid(row=0, column=13, padx=8, sticky="e")
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
        self.merge_tab.columnconfigure(0, weight=2)
        self.merge_tab.columnconfigure(1, weight=1)
        self.merge_tab.rowconfigure(1, weight=1)
        self.merge_status_var = tk.StringVar(value="No merged packets")
        ttk.Label(self.merge_tab, textvariable=self.merge_status_var, font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8)
        )
        self.map_placeholder = ttk.LabelFrame(self.merge_tab, text="Offline GPS Track")
        self.map_placeholder.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(self.map_placeholder, text="Chart added in Task 8").pack(expand=True)
        side = ttk.Frame(self.merge_tab)
        side.grid(row=1, column=1, sticky="nsew")
        self.readout_var = tk.StringVar(value="Lat/Lon: --\nSats: --\nVoltage: --\nRSSI: --")
        ttk.Label(side, textvariable=self.readout_var, justify="left").pack(anchor="w")
        event_frame = ttk.LabelFrame(side, text="Timeline Events")
        event_frame.pack(fill="x", pady=8)
        for label in ("Launch", "Apogee", "Deployment", "Landing"):
            ttk.Button(event_frame, text=label, command=lambda name=label: self._record_event(name)).pack(
                side="left", padx=2, pady=4
            )
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
        ttk.Label(chart_box, text="Charts added in Task 8").pack()
        tree = self._make_tree(tab, ("millis", "alt_baro", "voltage", "rssi", "snr"))
        tree.grid(row=2, column=0, sticky="nsew")
        setattr(self, f"{source}_tree", tree)

    def _make_tree(self, parent, columns: tuple[str, ...]) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=12)
        for column in columns:
            tree.heading(column, text=column)
            tree.column(column, width=90, anchor="center")
        return tree

    def _refresh_ports(self) -> None:
        ports = list_serial_ports()
        values = ports or [DEFAULT_PORT_1, DEFAULT_PORT_2]
        self.port1_combo["values"] = values
        self.port2_combo["values"] = values

    def _start_logging(self) -> None:
        if self.log_writer is None:
            self.log_writer = LogWriter()
            self.summary_var.set(f"Logging to {self.log_writer.log_dir}")

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
        reader = ReplayReader(Path(path), "port1", self.event_queue)
        self.replay_readers.append(reader)
        self.replay_threads.append(reader.start())

    def _record_event(self, name: str) -> None:
        timestamp = time.time()
        self.event_markers.append((name, timestamp))
        if self.log_writer is not None:
            self.log_writer.write_event(name)
        self.summary_var.set(f"Event: {name}")

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
        self._update_summary()
        self.after(100, self._drain_events)

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

    def _is_current_serial_event(self, source: str, event: dict) -> bool:
        generation = event.get("generation")
        if generation is None:
            return True
        return generation == self.reader_generations[source]

    def _handle_line(self, source: str, line: str, arrival_time: float) -> None:
        state = self.port_states[source]
        state.record_raw(line, arrival_time)
        if self.log_writer is not None:
            self.log_writer.write_raw(source, line)
        if line.startswith("#"):
            rssi, snr = TelemetryParser.parse_link_comment(line)
            state.record_link(rssi, snr)
            return
        try:
            packet = TelemetryParser.parse_packet(line, source, state.latest_rssi, state.latest_snr, arrival_time)
        except ValueError:
            state.record_malformed(line, arrival_time)
            self._update_port_view(source)
            return
        state.record_packet(packet)
        self.port_packets[source].append(packet)
        self.port_packets[source] = self.port_packets[source][-300:]
        previous_selected = self.merge_buffer.selected.get(packet.millis)
        selected = self.merge_buffer.add(packet)
        self._update_port_view(source, packet)
        if selected is not packet:
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

    def _update_port_view(self, source: str, packet_for_row: Optional[TelemetryPacket] = None) -> None:
        state = self.port_states[source]
        packet = state.latest_packet
        detail_var = getattr(self, f"{source}_detail_var")
        if packet is None:
            detail_var.set(f"{source}: {state.status}\nPackets: 0\nMalformed: {state.malformed_count}")
            return
        detail_var.set(
            f"{source}: {state.status}\n"
            f"Packets: {state.packet_count}  Malformed: {state.malformed_count}\n"
            f"Alt: {packet.alt_baro:.2f} m  Voltage: {packet.voltage:.3f} V  "
            f"RSSI: {packet.rssi if packet.rssi is not None else '--'}"
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
            f"RSSI: {packet.rssi if packet.rssi is not None else '--'}"
        )
        if rebuild_tree:
            for item in self.merged_tree.get_children():
                self.merged_tree.delete(item)
            for merged_packet in self.merged_packets[-200:]:
                self._insert_merged_row(merged_packet)
            return
        self._insert_merged_row(packet)

    def _insert_merged_row(self, packet: TelemetryPacket) -> None:
        self.merged_tree.insert(
            "",
            0,
            values=(packet.millis, packet.source, f"{packet.alt_baro:.2f}", f"{packet.voltage:.3f}", packet.rssi),
        )
        while len(self.merged_tree.get_children()) > 200:
            self.merged_tree.delete(self.merged_tree.get_children()[-1])

    def _update_summary(self) -> None:
        elapsed = int(time.time() - self.session_started)
        p1 = self.port_states["port1"].packet_count
        p2 = self.port_states["port2"].packet_count
        malformed = self.port_states["port1"].malformed_count + self.port_states["port2"].malformed_count
        self.summary_var.set(f"{elapsed}s  P1={p1}  P2={p2}  merged={self.merged_count}  malformed={malformed}")

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
