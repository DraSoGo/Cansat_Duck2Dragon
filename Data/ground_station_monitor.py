#!/usr/bin/env python3
"""Duck2Dragon Monitor - Tkinter dual-LoRa ground station dashboard."""

import csv
import math
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
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
        self.files = {
            "port1": self._open("port1"),
            "port2": self._open("port2"),
            "merged": self._open("merged"),
            "events": self._open("events"),
        }
        self._write_headers()

    def _open(self, suffix: str):
        return open(self.log_dir / f"{self.session_id}_{suffix}.csv", "a", buffering=1, newline="")

    def _write_headers(self) -> None:
        started = datetime.now().isoformat(timespec="seconds")
        for source in ("port1", "port2"):
            self.files[source].write(f"# session start {started}\n")
            self.files[source].write(f"# {CSV_HEADER}\n")
        self.files["merged"].write(f"# session start {started}\n")
        self.files["merged"].write(f"{CSV_HEADER},source,rssi,snr\n")
        self.files["events"].write(f"# session start {started}\n")
        self.files["events"].write("timestamp,event,note\n")

    def write_raw(self, source: str, line: str) -> None:
        if source not in ("port1", "port2"):
            raise ValueError(f"invalid raw log source: {source}")
        self.files[source].write(line.rstrip("\r\n") + "\n")

    def write_merged(self, packet: TelemetryPacket) -> None:
        rssi = "" if packet.rssi is None else str(packet.rssi)
        snr = "" if packet.snr is None else f"{packet.snr:.2f}"
        row = packet.csv_values() + [packet.source, rssi, snr]
        self.files["merged"].write(",".join(row) + "\n")

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
                time.sleep(self.reconnect_delay)
                continue
            self._read_loop()

    def _connect(self) -> bool:
        try:
            self.serial_obj = self.serial_factory(self.port, self.baud, timeout=1)
        except Exception as exc:
            self.event_queue.put({
                "type": "status",
                "source": self.source,
                "status": "reconnecting",
                "message": str(exc),
            })
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
            self.event_queue.put({
                "type": "status",
                "source": self.source,
                "status": "reconnecting",
                "message": str(exc),
            })
        finally:
            self._close_serial()

    def _close_serial(self) -> None:
        if self.serial_obj is not None:
            try:
                self.serial_obj.close()
            finally:
                self.serial_obj = None

    def stop(self) -> None:
        self.stop_event.set()
        self._close_serial()

    def run_once_for_test(self) -> None:
        if self._connect():
            assert self.serial_obj is not None
            raw = self.serial_obj.readline()
            if raw:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                self.event_queue.put({
                    "type": "line",
                    "source": self.source,
                    "line": line,
                    "arrival_time": time.time(),
                    "mode": "serial",
                })
            self._close_serial()

    def try_connect_once_for_test(self) -> None:
        self._connect()
        self._close_serial()


def main() -> int:
    print("Duck2Dragon Monitor parser module loaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
