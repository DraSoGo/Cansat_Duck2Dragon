# Ground Station Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved `Duck2Dragon Monitor` Tkinter GUI for two LoRa serial receivers, best-RSSI merge, logging, replay, charts, offline GPS track, alerts, and flight event timeline.

**Architecture:** Create one runnable GUI module at `Data/ground_station_monitor.py` with small internal classes for parsing, merge, logging, replay, serial workers, state, and Tkinter UI. Add focused standard-library `unittest` coverage in `tests/test_ground_station_monitor.py` so parser, merge, log, replay, and serial-worker behavior can be verified without hardware.

**Tech Stack:** Python 3, Tkinter, pyserial, matplotlib, standard-library `unittest`, standard-library `threading`, `queue`, `csv`, `tempfile`, and `pathlib`.

---

## Approved Spec

Implementation must follow:

```text
docs/superpowers/specs/2026-06-05-ground-station-monitor-design.md
```

## File Structure

- Create: `Data/ground_station_monitor.py`
  - Runnable Tkinter GUI entry point.
  - Contains `TelemetryPacket`, `PortState`, `TelemetryParser`, `MergeBuffer`, `LogWriter`, `ReplayReader`, `SerialReader`, alert helpers, and `GroundStationMonitorApp`.
  - Uses `if __name__ == "__main__": main()` so tests can import it without opening a window.
- Create: `tests/test_ground_station_monitor.py`
  - Unit tests using only standard-library `unittest`.
  - Imports `Data/ground_station_monitor.py` via `importlib.util.spec_from_file_location`.
  - Tests parser, RSSI/SNR parsing, merge behavior, log writing, replay event production, and serial-reader reconnect behavior with fakes.
- Modify: `README.md`
  - Add a short GUI monitor usage section under the existing data logger instructions.
- Leave unchanged:
  - `Data/read_serial_1.py`
  - `Data/read_serial_2.py`

## Task 1: Parser And Telemetry Model

**Files:**
- Create: `tests/test_ground_station_monitor.py`
- Create: `Data/ground_station_monitor.py`

- [ ] **Step 1: Write failing parser tests**

Create `tests/test_ground_station_monitor.py` with this content:

```python
import importlib.util
import pathlib
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "Data" / "ground_station_monitor.py"


def load_monitor_module():
    spec = importlib.util.spec_from_file_location("ground_station_monitor", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def test_parse_valid_24_field_packet(self):
        line = (
            "128518,8.367500,100.043922,-1.80,12,56.02,24.98,1006.54,"
            "0.1000,0.2000,0.3000,-0.1445,0.2070,0.2324,"
            "1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,3.684,-90.500,-0.333"
        )

        packet = self.monitor.TelemetryParser.parse_packet(
            line,
            source="port1",
            rssi=-61,
            snr=11.75,
            arrival_time=1000.0,
        )

        self.assertEqual(packet.millis, 128518)
        self.assertEqual(packet.source, "port1")
        self.assertAlmostEqual(packet.lat, 8.367500)
        self.assertAlmostEqual(packet.lon, 100.043922)
        self.assertEqual(packet.sats, 12)
        self.assertAlmostEqual(packet.voltage, 3.684)
        self.assertAlmostEqual(packet.current, -90.500)
        self.assertAlmostEqual(packet.watt, -0.333)
        self.assertEqual(packet.rssi, -61)
        self.assertAlmostEqual(packet.snr, 11.75)

    def test_rejects_wrong_field_count(self):
        with self.assertRaises(ValueError) as ctx:
            self.monitor.TelemetryParser.parse_packet(
                "1,2,3",
                source="port1",
                rssi=None,
                snr=None,
                arrival_time=1000.0,
            )

        self.assertIn("expected 24 fields", str(ctx.exception))

    def test_parse_rssi_snr_comment(self):
        result = self.monitor.TelemetryParser.parse_link_comment("# RSSI=-67 SNR=10.25")

        self.assertEqual(result, (-67, 10.25))

    def test_ignores_non_link_comment(self):
        result = self.monitor.TelemetryParser.parse_link_comment("# Ground Station ready")

        self.assertEqual(result, (None, None))
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor.ParserTests -v
```

Expected:

```text
FileNotFoundError
```

or:

```text
AttributeError: module 'ground_station_monitor' has no attribute 'TelemetryParser'
```

- [ ] **Step 3: Implement parser and telemetry model**

Create `Data/ground_station_monitor.py` with this initial content:

```python
#!/usr/bin/env python3
"""Duck2Dragon Monitor - Tkinter dual-LoRa ground station dashboard."""

from __future__ import annotations

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


def main() -> int:
    print("Duck2Dragon Monitor parser module loaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run parser tests**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor.ParserTests -v
```

Expected:

```text
OK
```

- [ ] **Step 5: Commit**

Run:

```bash
git add Data/ground_station_monitor.py tests/test_ground_station_monitor.py
git commit -m "Add ground station telemetry parser"
```

## Task 2: Best-RSSI Merge Buffer

**Files:**
- Modify: `tests/test_ground_station_monitor.py`
- Modify: `Data/ground_station_monitor.py`

- [ ] **Step 1: Add failing merge tests**

Append this class to `tests/test_ground_station_monitor.py`:

```python
class MergeBufferTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def packet(self, millis, source, rssi, arrival=1000.0):
        line = (
            f"{millis},8.367500,100.043922,-1.80,12,56.02,24.98,1006.54,"
            "0.1000,0.2000,0.3000,-0.1445,0.2070,0.2324,"
            "1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,3.684,-90.500,-0.333"
        )
        return self.monitor.TelemetryParser.parse_packet(line, source, rssi, 11.0, arrival)

    def test_keeps_first_unique_packet(self):
        buffer = self.monitor.MergeBuffer()
        packet = self.packet(100, "port1", -70)

        selected = buffer.add(packet)

        self.assertIs(selected, packet)
        self.assertEqual(buffer.selected[100].source, "port1")

    def test_duplicate_with_higher_rssi_replaces_selected(self):
        buffer = self.monitor.MergeBuffer()
        weak = self.packet(100, "port1", -80, arrival=1000.0)
        strong = self.packet(100, "port2", -55, arrival=1000.1)

        buffer.add(weak)
        selected = buffer.add(strong)

        self.assertIs(selected, strong)
        self.assertEqual(buffer.selected[100].source, "port2")

    def test_duplicate_with_lower_rssi_does_not_replace_selected(self):
        buffer = self.monitor.MergeBuffer()
        strong = self.packet(100, "port1", -55)
        weak = self.packet(100, "port2", -80)

        buffer.add(strong)
        selected = buffer.add(weak)

        self.assertIs(selected, strong)
        self.assertEqual(buffer.selected[100].source, "port1")

    def test_rssi_missing_loses_to_available_rssi(self):
        buffer = self.monitor.MergeBuffer()
        missing = self.packet(100, "port1", None)
        known = self.packet(100, "port2", -90)

        buffer.add(missing)
        selected = buffer.add(known)

        self.assertIs(selected, known)

    def test_both_rssi_missing_keeps_first(self):
        buffer = self.monitor.MergeBuffer()
        first = self.packet(100, "port1", None, arrival=1000.0)
        second = self.packet(100, "port2", None, arrival=1000.1)

        buffer.add(first)
        selected = buffer.add(second)

        self.assertIs(selected, first)
```

- [ ] **Step 2: Run merge tests to verify failure**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor.MergeBufferTests -v
```

Expected:

```text
AttributeError: module 'ground_station_monitor' has no attribute 'MergeBuffer'
```

- [ ] **Step 3: Implement `MergeBuffer`**

Add this code below `TelemetryParser` in `Data/ground_station_monitor.py`:

```python
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
```

- [ ] **Step 4: Run merge and parser tests**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor.ParserTests tests.test_ground_station_monitor.MergeBufferTests -v
```

Expected:

```text
OK
```

- [ ] **Step 5: Commit**

Run:

```bash
git add Data/ground_station_monitor.py tests/test_ground_station_monitor.py
git commit -m "Add best RSSI merge buffer"
```

## Task 3: Log Writer

**Files:**
- Modify: `tests/test_ground_station_monitor.py`
- Modify: `Data/ground_station_monitor.py`

- [ ] **Step 1: Add failing log writer tests**

Append this class to `tests/test_ground_station_monitor.py`:

```python
class LogWriterTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def packet(self):
        line = (
            "128518,8.367500,100.043922,-1.80,12,56.02,24.98,1006.54,"
            "0.1000,0.2000,0.3000,-0.1445,0.2070,0.2324,"
            "1.0000,0.0000,0.0000,0.00,0.00,0.00,0.00,3.684,-90.500,-0.333"
        )
        return self.monitor.TelemetryParser.parse_packet(line, "port1", -61, 11.75, 1000.0)

    def test_log_writer_creates_four_session_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = self.monitor.LogWriter(pathlib.Path(tmp), session_id="2026-06-05_12-00-00")
            writer.close()

            names = sorted(path.name for path in pathlib.Path(tmp).iterdir())

        self.assertEqual(
            names,
            [
                "2026-06-05_12-00-00_events.csv",
                "2026-06-05_12-00-00_merged.csv",
                "2026-06-05_12-00-00_port1.csv",
                "2026-06-05_12-00-00_port2.csv",
            ],
        )

    def test_raw_log_preserves_malformed_and_comment_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = self.monitor.LogWriter(pathlib.Path(tmp), session_id="session")
            writer.write_raw("port1", "# RSSI=-61 SNR=11.75")
            writer.write_raw("port1", "malformed,line")
            writer.close()

            text = (pathlib.Path(tmp) / "session_port1.csv").read_text()

        self.assertIn("# RSSI=-61 SNR=11.75", text)
        self.assertIn("malformed,line", text)

    def test_merged_log_includes_source_and_link_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = self.monitor.LogWriter(pathlib.Path(tmp), session_id="session")
            writer.write_merged(self.packet())
            writer.close()

            text = (pathlib.Path(tmp) / "session_merged.csv").read_text()

        self.assertIn("source,rssi,snr", text)
        self.assertIn("port1,-61,11.75", text)
```

- [ ] **Step 2: Run log tests to verify failure**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor.LogWriterTests -v
```

Expected:

```text
AttributeError: module 'ground_station_monitor' has no attribute 'LogWriter'
```

- [ ] **Step 3: Implement `LogWriter`**

Add this code below `MergeBuffer`:

```python
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
        return open(self.log_dir / f"{self.session_id}_{suffix}.csv", "a", buffering=1)

    def _write_headers(self) -> None:
        started = datetime.now().isoformat(timespec="seconds")
        for source in ("port1", "port2"):
            self.files[source].write(f"# session start {started}\n")
            self.files[source].write(f"# {CSV_HEADER}\n")
        self.files["merged"].write(f"# session start {started}\n")
        self.files["merged"].write(f"{CSV_HEADER},source,rssi,snr\n")
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
        safe_note = note.replace("\n", " ").replace("\r", " ")
        self.files["events"].write(f"{timestamp},{event},{safe_note}\n")

    def close(self) -> None:
        for file_obj in self.files.values():
            file_obj.close()
```

- [ ] **Step 4: Run full tests**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor -v
```

Expected:

```text
OK
```

- [ ] **Step 5: Commit**

Run:

```bash
git add Data/ground_station_monitor.py tests/test_ground_station_monitor.py
git commit -m "Add monitor session logging"
```

## Task 4: Replay Reader

**Files:**
- Modify: `tests/test_ground_station_monitor.py`
- Modify: `Data/ground_station_monitor.py`

- [ ] **Step 1: Add failing replay tests**

Append this class to `tests/test_ground_station_monitor.py`:

```python
class ReplayReaderTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def test_replay_reader_emits_line_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "sample.csv"
            path.write_text("# RSSI=-61 SNR=11.75\n1,2,3\n")
            events = queue.Queue()

            reader = self.monitor.ReplayReader(path, "port1", events, speed=99.0)
            reader.run_once_for_test()

            first = events.get_nowait()
            second = events.get_nowait()

        self.assertEqual(first["type"], "line")
        self.assertEqual(first["source"], "port1")
        self.assertEqual(first["line"], "# RSSI=-61 SNR=11.75")
        self.assertEqual(second["line"], "1,2,3")

    def test_replay_reader_skip_empty_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "sample.csv"
            path.write_text("\n\n# boot\n")
            events = queue.Queue()

            reader = self.monitor.ReplayReader(path, "port2", events, speed=99.0)
            reader.run_once_for_test()

            event = events.get_nowait()

        self.assertEqual(event["line"], "# boot")
        self.assertTrue(events.empty())
```

- [ ] **Step 2: Run replay tests to verify failure**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor.ReplayReaderTests -v
```

Expected:

```text
AttributeError: module 'ground_station_monitor' has no attribute 'ReplayReader'
```

- [ ] **Step 3: Implement `ReplayReader`**

Add this code below `LogWriter`:

```python
class ReplayReader:
    def __init__(self, path: Path, source: str, event_queue: queue.Queue, speed: float = 1.0):
        self.path = Path(path)
        self.source = source
        self.event_queue = event_queue
        self.speed = max(speed, 0.1)
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name=f"ReplayReader-{self.source}", daemon=True)
        thread.start()
        return thread

    def run(self) -> None:
        self.event_queue.put({"type": "status", "source": self.source, "status": "replay"})
        last_emit = None
        for line in self._iter_lines():
            if self.stop_event.is_set():
                break
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.05)
            now = time.time()
            if last_emit is not None:
                time.sleep(min(0.5, (now - last_emit) / self.speed))
            last_emit = time.time()
            self.event_queue.put({
                "type": "line",
                "source": self.source,
                "line": line,
                "arrival_time": time.time(),
                "mode": "replay",
            })
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

    def stop(self) -> None:
        self.stop_event.set()

    def pause(self) -> None:
        self.pause_event.set()

    def resume(self) -> None:
        self.pause_event.clear()
```

- [ ] **Step 4: Run full tests**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor -v
```

Expected:

```text
OK
```

- [ ] **Step 5: Commit**

Run:

```bash
git add Data/ground_station_monitor.py tests/test_ground_station_monitor.py
git commit -m "Add monitor replay reader"
```

## Task 5: Serial Reader With Auto-Reconnect

**Files:**
- Modify: `tests/test_ground_station_monitor.py`
- Modify: `Data/ground_station_monitor.py`

- [ ] **Step 1: Add failing serial-reader tests**

Append this class to `tests/test_ground_station_monitor.py`:

```python
class FakeSerial:
    def __init__(self, lines):
        self.lines = list(lines)
        self.closed = False

    def readline(self):
        if not self.lines:
            return b""
        item = self.lines.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


class SerialReaderTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def test_serial_reader_emits_decoded_line(self):
        events = queue.Queue()
        fake = FakeSerial([b"# RSSI=-61 SNR=11.75\r\n"])
        reader = self.monitor.SerialReader(
            source="port1",
            port="/dev/fake",
            baud=115200,
            event_queue=events,
            serial_factory=lambda port, baud, timeout: fake,
            reconnect_delay=0.01,
        )

        reader.run_once_for_test()

        status = events.get_nowait()
        line = events.get_nowait()
        self.assertEqual(status["status"], "connected")
        self.assertEqual(line["line"], "# RSSI=-61 SNR=11.75")

    def test_serial_reader_reports_reconnecting_on_factory_error(self):
        events = queue.Queue()

        def factory(port, baud, timeout):
            raise OSError("missing device")

        reader = self.monitor.SerialReader(
            source="port2",
            port="/dev/missing",
            baud=115200,
            event_queue=events,
            serial_factory=factory,
            reconnect_delay=0.01,
        )

        reader.try_connect_once_for_test()

        event = events.get_nowait()
        self.assertEqual(event["type"], "status")
        self.assertEqual(event["source"], "port2")
        self.assertEqual(event["status"], "reconnecting")
```

- [ ] **Step 2: Run serial tests to verify failure**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor.SerialReaderTests -v
```

Expected:

```text
AttributeError: module 'ground_station_monitor' has no attribute 'SerialReader'
```

- [ ] **Step 3: Implement `SerialReader`**

Add this code below `ReplayReader`:

```python
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
```

- [ ] **Step 4: Run full tests**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor -v
```

Expected:

```text
OK
```

- [ ] **Step 5: Commit**

Run:

```bash
git add Data/ground_station_monitor.py tests/test_ground_station_monitor.py
git commit -m "Add reconnecting serial reader"
```

## Task 6: Port State, Alerts, And Event Handling

**Files:**
- Modify: `tests/test_ground_station_monitor.py`
- Modify: `Data/ground_station_monitor.py`

- [ ] **Step 1: Add failing state and alert tests**

Append this class to `tests/test_ground_station_monitor.py`:

```python
class StateAndAlertTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def packet(self, voltage=3.8, sats=8, rssi=-60, arrival=1000.0):
        line = (
            f"128518,8.367500,100.043922,-1.80,{sats},56.02,24.98,1006.54,"
            "0.1000,0.2000,0.3000,-0.1445,0.2070,0.2324,"
            f"1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,{voltage:.3f},-90.500,-0.333"
        )
        return self.monitor.TelemetryParser.parse_packet(line, "port1", rssi, 11.75, arrival)

    def test_port_state_counts_packets_and_malformed_lines(self):
        state = self.monitor.PortState("port1")
        state.record_packet(self.packet())
        state.record_malformed("bad,line")

        self.assertEqual(state.packet_count, 1)
        self.assertEqual(state.malformed_count, 1)
        self.assertEqual(state.latest_packet.millis, 128518)

    def test_alerts_flag_low_voltage_weak_rssi_no_gps_and_stale(self):
        packet = self.packet(voltage=3.3, sats=0, rssi=-120, arrival=900.0)
        alerts = self.monitor.evaluate_alerts(packet, now=1005.0)

        self.assertIn("low_voltage", alerts)
        self.assertIn("weak_rssi", alerts)
        self.assertIn("no_gps_lock", alerts)
        self.assertIn("stale_packet", alerts)
```

- [ ] **Step 2: Run state tests to verify failure**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor.StateAndAlertTests -v
```

Expected:

```text
AttributeError
```

- [ ] **Step 3: Implement state and alert helpers**

Add this code above `TelemetryParser`:

```python
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

    def record_malformed(self, line: str) -> None:
        self.latest_raw_line = line
        self.malformed_count += 1
        self.recent_malformed += 1
```

Add this code below `PortState`:

```python
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
```

- [ ] **Step 4: Run full tests**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor -v
```

Expected:

```text
OK
```

- [ ] **Step 5: Commit**

Run:

```bash
git add Data/ground_station_monitor.py tests/test_ground_station_monitor.py
git commit -m "Add monitor state and alerts"
```

## Task 7: Tkinter UI Shell And Event Pipeline

**Files:**
- Modify: `Data/ground_station_monitor.py`

- [ ] **Step 1: Add imports for Tkinter and serial port scanning**

Add these imports after the existing imports:

```python
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
```

Add this helper below `default_serial_factory`:

```python
def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    return [port.device for port in sorted(list_ports.comports())]
```

- [ ] **Step 2: Add the `GroundStationMonitorApp` class**

Add this class below `SerialReader`:

```python
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
        self.reader_threads: list[threading.Thread] = []
        self.replay_readers: list[ReplayReader] = []
        self.session_started = time.time()
        self.merged_count = 0
        self.event_markers: list[tuple[str, float]] = []
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
```

- [ ] **Step 3: Add event handling methods**

Add these methods inside `GroundStationMonitorApp`:

```python
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
        self._start_logging()
        port = self.port_vars[source].get().strip()
        baud = int(self.baud_vars[source].get().strip())
        reader = SerialReader(source, port, baud, self.event_queue)
        self.readers[source] = reader
        self.reader_threads.append(reader.start())

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
        reader.start()

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
        if event_type == "status" and source in self.port_states:
            state = self.port_states[source]
            state.record_status(event.get("status", "offline"), event.get("message", ""))
            self.status_vars[source].set(state.status)
            return
        if event_type == "line" and source in self.port_states:
            self._handle_line(source, event["line"], event.get("arrival_time", time.time()))

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
            state.record_malformed(line)
            return
        state.record_packet(packet)
        self.port_packets[source].append(packet)
        self.port_packets[source] = self.port_packets[source][-300:]
        selected = self.merge_buffer.add(packet)
        if selected is packet:
            self.merged_count += 1
            self.merged_packets.append(packet)
            self.merged_packets = self.merged_packets[-300:]
            if self.log_writer is not None:
                self.log_writer.write_merged(packet)
        self._update_port_view(source)
        self._update_merge_view()

    def _update_port_view(self, source: str) -> None:
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
        tree = getattr(self, f"{source}_tree")
        tree.insert("", 0, values=(packet.millis, f"{packet.alt_baro:.2f}", f"{packet.voltage:.3f}", packet.rssi, packet.snr))
        while len(tree.get_children()) > 200:
            tree.delete(tree.get_children()[-1])

    def _update_merge_view(self) -> None:
        if not self.merged_packets:
            return
        packet = self.merged_packets[-1]
        alerts = evaluate_alerts(packet)
        self.merge_status_var.set(f"Merged packets: {self.merged_count}  Alerts: {', '.join(sorted(alerts)) or 'none'}")
        self.readout_var.set(
            f"Lat/Lon: {packet.lat:.6f}, {packet.lon:.6f}\n"
            f"Sats: {packet.sats}\n"
            f"Voltage: {packet.voltage:.3f} V\n"
            f"RSSI: {packet.rssi if packet.rssi is not None else '--'}"
        )
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
        for reader in self.readers.values():
            reader.stop()
        for reader in self.replay_readers:
            reader.stop()
        if self.log_writer is not None:
            self.log_writer.close()
        super().destroy()
```

- [ ] **Step 4: Replace `main()` with GUI startup**

Replace the current `main()` with:

```python
def main() -> int:
    app = GroundStationMonitorApp()
    app.mainloop()
    return 0
```

- [ ] **Step 5: Run unit tests**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor -v
```

Expected:

```text
OK
```

- [ ] **Step 6: Run GUI smoke check**

Run:

```bash
python3 Data/ground_station_monitor.py
```

Expected:

```text
The Duck2Dragon Monitor window opens with three tabs: Merge Data, Port 1, and Port 2.
```

Close the window manually after verifying.

- [ ] **Step 7: Commit**

Run:

```bash
git add Data/ground_station_monitor.py
git commit -m "Add Duck2Dragon monitor UI shell"
```

## Task 8: Matplotlib Charts And Offline GPS Track

**Files:**
- Modify: `Data/ground_station_monitor.py`

- [ ] **Step 1: Add matplotlib imports**

Add these imports after the Tkinter imports:

```python
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
```

- [ ] **Step 2: Add chart creation helper inside `GroundStationMonitorApp`**

Add this method inside the class:

```python
    def _make_figure(self, parent, title: str, ylabel: str):
        figure = Figure(figsize=(4, 2.4), dpi=100)
        axis = figure.add_subplot(111)
        axis.set_title(title)
        axis.set_xlabel("sample")
        axis.set_ylabel(ylabel)
        axis.grid(True, alpha=0.3)
        canvas = FigureCanvasTkAgg(figure, master=parent)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        return figure, axis, canvas
```

- [ ] **Step 3: Replace merge tab chart placeholder with figures**

In `_build_merge_tab`, replace the `self.map_placeholder` block with:

```python
        chart_area = ttk.Frame(self.merge_tab)
        chart_area.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        chart_area.columnconfigure(0, weight=1)
        chart_area.rowconfigure(0, weight=2)
        chart_area.rowconfigure(1, weight=1)

        gps_frame = ttk.LabelFrame(chart_area, text="Offline GPS Track")
        gps_frame.grid(row=0, column=0, sticky="nsew")
        self.gps_fig, self.gps_ax, self.gps_canvas = self._make_figure(gps_frame, "GPS Track", "relative north")

        lower_charts = ttk.Frame(chart_area)
        lower_charts.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        lower_charts.columnconfigure(0, weight=1)
        lower_charts.columnconfigure(1, weight=1)
        alt_frame = ttk.LabelFrame(lower_charts, text="Altitude")
        alt_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        link_frame = ttk.LabelFrame(lower_charts, text="Link Quality")
        link_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self.alt_fig, self.alt_ax, self.alt_canvas = self._make_figure(alt_frame, "Altitude", "m")
        self.link_fig, self.link_ax, self.link_canvas = self._make_figure(link_frame, "RSSI", "dBm")
```

- [ ] **Step 4: Replace port tab chart placeholder with figures**

In `_build_port_tab`, replace the `chart_box` placeholder block with:

```python
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
```

- [ ] **Step 5: Add chart refresh methods**

Add these methods inside `GroundStationMonitorApp`:

```python
    def _refresh_merge_charts(self) -> None:
        packets = self.merged_packets[-100:]
        gps_packets = [packet for packet in packets if packet.gps_valid]

        self.gps_ax.clear()
        self.gps_ax.set_title("GPS Track")
        self.gps_ax.grid(True, alpha=0.3)
        if gps_packets:
            origin_lat = gps_packets[0].lat
            origin_lon = gps_packets[0].lon
            xs = [(packet.lon - origin_lon) * 111_320 for packet in gps_packets]
            ys = [(packet.lat - origin_lat) * 110_540 for packet in gps_packets]
            self.gps_ax.plot(xs, ys, marker="o", linewidth=1)
            self.gps_ax.set_xlabel("relative east (m)")
            self.gps_ax.set_ylabel("relative north (m)")
        else:
            self.gps_ax.text(0.5, 0.5, "No GPS lock", ha="center", va="center", transform=self.gps_ax.transAxes)
        self.gps_canvas.draw_idle()

        self.alt_ax.clear()
        self.alt_ax.set_title("Altitude")
        self.alt_ax.grid(True, alpha=0.3)
        self.alt_ax.plot([packet.alt_baro for packet in packets], label="baro")
        self.alt_ax.plot([packet.alt_gps for packet in packets], label="gps")
        self.alt_ax.legend(loc="upper left")
        self.alt_canvas.draw_idle()

        self.link_ax.clear()
        self.link_ax.set_title("RSSI")
        self.link_ax.grid(True, alpha=0.3)
        p1 = [packet.rssi for packet in packets if packet.source == "port1" and packet.rssi is not None]
        p2 = [packet.rssi for packet in packets if packet.source == "port2" and packet.rssi is not None]
        self.link_ax.plot(p1, label="Port 1")
        self.link_ax.plot(p2, label="Port 2")
        self.link_ax.legend(loc="lower left")
        self.link_canvas.draw_idle()

    def _refresh_port_charts(self, source: str) -> None:
        packets = self.port_packets[source][-100:]
        figures = getattr(self, f"{source}_figures")
        series = {
            "altitude": [packet.alt_baro for packet in packets],
            "voltage": [packet.voltage for packet in packets],
            "rssi": [packet.rssi for packet in packets if packet.rssi is not None],
        }
        for key, values in series.items():
            _fig, axis, canvas = figures[key]
            axis.clear()
            axis.set_title(key.title())
            axis.grid(True, alpha=0.3)
            axis.plot(values)
            canvas.draw_idle()
```

- [ ] **Step 6: Call chart refreshes from existing update methods**

At the end of `_update_port_view`, add:

```python
        self._refresh_port_charts(source)
```

At the end of `_update_merge_view`, add:

```python
        self._refresh_merge_charts()
```

- [ ] **Step 7: Run unit tests**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor -v
```

Expected:

```text
OK
```

- [ ] **Step 8: Run GUI smoke check**

Run:

```bash
python3 Data/ground_station_monitor.py
```

Expected:

```text
The Merge Data tab shows an Offline GPS Track panel, Altitude chart, and Link Quality chart.
Each port tab shows Altitude, Voltage, and RSSI charts.
```

Close the window manually after verifying.

- [ ] **Step 9: Commit**

Run:

```bash
git add Data/ground_station_monitor.py
git commit -m "Add monitor charts and GPS track"
```

## Task 9: Replay Controls And README Usage

**Files:**
- Modify: `Data/ground_station_monitor.py`
- Modify: `README.md`

- [ ] **Step 1: Add replay speed UI state**

In `_build_controls`, after `self.summary_var`, add:

```python
        self.replay_speed_var = tk.StringVar(value="1.0")
```

Add this control near the Replay button:

```python
        ttk.Label(frame, text="Replay x").grid(row=0, column=14, padx=2)
        ttk.Entry(frame, textvariable=self.replay_speed_var, width=5).grid(row=0, column=15, padx=2)
```

- [ ] **Step 2: Use replay speed when opening logs**

Replace the reader creation line in `_choose_replay_file`:

```python
        reader = ReplayReader(Path(path), "port1", self.event_queue)
```

with:

```python
        try:
            speed = float(self.replay_speed_var.get())
        except ValueError:
            speed = 1.0
            self.replay_speed_var.set("1.0")
        reader = ReplayReader(Path(path), "port1", self.event_queue, speed=speed)
```

- [ ] **Step 3: Add README GUI instructions**

In `README.md`, under the existing "Running the data logger" section, add this markdown:

````markdown
### Running the Tkinter ground station monitor

```bash
cd Data
python3 -m pip install pyserial matplotlib
python3 ground_station_monitor.py
```

The GUI is titled **Duck2Dragon Monitor**. It can read two serial ports at once, show `Merge Data`, `Port 1`, and `Port 2` tabs, save raw and merged logs under `Data/logs/`, and replay an existing log without connected hardware.
````

- [ ] **Step 4: Run unit tests**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor -v
```

Expected:

```text
OK
```

- [ ] **Step 5: Commit**

Run:

```bash
git add Data/ground_station_monitor.py README.md
git commit -m "Document ground station monitor usage"
```

## Task 10: Final Verification

**Files:**
- Verify: `Data/ground_station_monitor.py`
- Verify: `tests/test_ground_station_monitor.py`
- Verify: `README.md`

- [ ] **Step 1: Run all tests**

Run:

```bash
python3 -m unittest tests.test_ground_station_monitor -v
```

Expected:

```text
OK
```

- [ ] **Step 2: Check syntax compilation**

Run:

```bash
python3 -m py_compile Data/ground_station_monitor.py tests/test_ground_station_monitor.py
```

Expected:

```text
No output and exit code 0.
```

- [ ] **Step 3: Check git diff**

Run:

```bash
git diff --stat HEAD
```

Expected:

```text
No output if every task was committed.
```

- [ ] **Step 4: Manual GUI launch**

Run:

```bash
python3 Data/ground_station_monitor.py
```

Expected:

```text
Window title: Duck2Dragon Monitor
Tabs: Merge Data, Port 1, Port 2
Controls: Port 1 dropdown, Port 2 dropdown, baud entries, Refresh Ports, Start Logging, Replay Log
```

Close the window manually.

- [ ] **Step 5: Final status check**

Run:

```bash
git status --short
```

Expected:

```text
Only intentionally untracked visual companion files may remain under .superpowers/.
```

## Self-Review Notes

Spec coverage:

- Dual serial readers: Tasks 5 and 7.
- Three tabs: Task 7.
- Best-RSSI merge by `millis`: Task 2.
- Raw Port 1, raw Port 2, merged logs, and events: Task 3.
- Replay mode: Tasks 4 and 9.
- Full dashboard with Map First GPS track and charts: Tasks 7 and 8.
- Manual event buttons: Task 7.
- Fixed alerts: Task 6.
- Keep `read_serial_1.py` and `read_serial_2.py` unchanged: file structure and tasks avoid those paths.
- README run instructions: Task 9.

Placeholder scan:

- The plan contains no unfinished marker tokens or placeholder steps.
- Numeric alert thresholds are explicitly defined in Task 6.
- Commands include expected results.

Type consistency:

- `TelemetryPacket`, `TelemetryParser`, `MergeBuffer`, `LogWriter`, `ReplayReader`, `SerialReader`, `PortState`, and `GroundStationMonitorApp` names are consistent across tests and implementation steps.
- Queue events consistently use `type`, `source`, `line`, `arrival_time`, `status`, and `message`.
