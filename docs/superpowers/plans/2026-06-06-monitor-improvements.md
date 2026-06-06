# Ground Station Monitor Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor ground station monitor with bug fixes, new features (CSV export, packet loss, auto-apogee, configurable alerts), and extract core logic to `telemetry_core.py`

**Architecture:** Hybrid approach - fix bugs inline, extract testable classes (TelemetryPacket, Parser, MergeBuffer, LogWriter, ApogeeDetector) to separate module, add competition features with minimal GUI changes

**Tech Stack:** Python 3, Tkinter, matplotlib, numpy, pyserial, dataclasses

---

## File Structure

**New files:**
- `Data/telemetry_core.py` - Core telemetry logic (450 lines): TelemetryPacket, PortState, TelemetryParser, MergeBuffer, LogWriter, ApogeeDetector, alert evaluation, orientation math
- `Data/test_telemetry_core.py` - Unit tests for extracted core logic

**Modified files:**
- `Data/ground_station_monitor.py` - Remove extracted code, add imports, add new features (export, packet loss, apogee, alert config)

---

## Task 1: Create telemetry_core.py skeleton

**Files:**
- Create: `Data/telemetry_core.py`

- [ ] **Step 1: Create file with imports and constants**

```python
#!/usr/bin/env python3
"""Duck2Dragon telemetry core - packet parsing, merging, logging, alerts."""

import csv
import math
import re
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
```

- [ ] **Step 2: Commit skeleton**

```bash
git add Data/telemetry_core.py
git commit -m "feat: create telemetry_core module skeleton"
```

---

## Task 2: Add TelemetryPacket dataclass

**Files:**
- Modify: `Data/telemetry_core.py`

- [ ] **Step 1: Add TelemetryPacket dataclass**

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add Data/telemetry_core.py
git commit -m "feat: add TelemetryPacket with fixed GPS validation"
```

---

## Task 3: Add PortState dataclass

**Files:**
- Modify: `Data/telemetry_core.py`

- [ ] **Step 1: Add PortState dataclass after TelemetryPacket**

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add Data/telemetry_core.py
git commit -m "feat: add PortState with packet loss tracking field"
```

---

## Remaining Tasks Summary

**Task 4-10:** Complete `telemetry_core.py` extraction
- Task 4: TelemetryParser (copy lines 267-371 from monitor)
- Task 5: Alert functions (evaluate_alerts, evaluate_port_alerts)
- Task 6: MergeBuffer class (lines 374-407)
- Task 7: LogWriter class (lines 410-487)
- Task 8: Orientation math functions (quaternion_rotation_matrix, rotation_euler_degrees, orientation_label, packet_orientation_rotation, packet_has_default_orientation)
- Task 9: ApogeeDetector class (new, per spec section 2C)
- Task 10: AlertConfig dataclass (new, per spec section 2D)

**Task 11-15:** Bug fixes in monitor
- Task 11: Remove dead constants (lines 75-81)
- Task 12: Fix drag sensitivity constant (0.6 → 0.3)
- Task 13: Add input validation to _connect_port
- Task 14: Update imports from telemetry_core
- Task 15: Remove extracted code from monitor

**Task 16-19:** New features
- Task 16: Export button + _export_merged_csv method
- Task 17: Packet loss display (calculate_packet_loss helper)
- Task 18: Apogee detector integration
- Task 19: Alert settings dialog + persistence

**Task 20:** Final testing
- Run verification plan from spec
- Hardware integration test

---

## Detailed Implementation (Tasks 4-20)

### Task 4: Add TelemetryParser to telemetry_core.py

- [ ] Copy TelemetryParser class from monitor.py lines 267-371
- [ ] Add `import time` at top of parse_packet method body
- [ ] Commit: `git commit -m "feat: add TelemetryParser to core"`

### Task 5: Add alert evaluation functions

```python
def evaluate_alerts(packet: Optional[TelemetryPacket], now: Optional[float] = None, config: Optional['AlertConfig'] = None) -> set[str]:
    if packet is None:
        return {"no_packet"}
    
    if config is None:
        config = AlertConfig()
    
    import time
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
```

- [ ] Commit: `git commit -m "feat: add alert evaluation with configurable thresholds"`

### Task 6: Add MergeBuffer

- [ ] Copy MergeBuffer class from monitor.py lines 374-407
- [ ] Commit: `git commit -m "feat: add MergeBuffer to core"`

### Task 7: Add LogWriter

- [ ] Copy LogWriter class from monitor.py lines 410-487
- [ ] Commit: `git commit -m "feat: add LogWriter to core"`

### Task 8: Add orientation math

```python
def quaternion_rotation_matrix(quaternion) -> 'np.ndarray':
    import numpy as np
    w, x, y, z = quaternion
    return np.array(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
            [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
            [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
        ],
        dtype=float,
    )

def rotation_euler_degrees(rotation) -> tuple[float, float, float]:
    pitch_sin = -float(rotation[2, 0])
    pitch_sin = min(1.0, max(-1.0, pitch_sin))
    pitch = math.asin(pitch_sin)
    if abs(math.cos(pitch)) > 1e-6:
        roll = math.atan2(float(rotation[2, 1]), float(rotation[2, 2]))
        yaw = math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))
    else:
        roll = 0.0
        yaw = math.atan2(-float(rotation[0, 1]), float(rotation[1, 1]))
    return tuple(math.degrees(value) for value in (roll, pitch, yaw))

def orientation_label(axis) -> tuple[str, float]:
    vertical_component = min(1.0, max(-1.0, abs(float(axis[2]))))
    tilt_degrees = math.degrees(math.acos(vertical_component))
    if tilt_degrees <= ORIENTATION_VERTICAL_DEGREES:
        label = "Vertical"
    elif tilt_degrees >= ORIENTATION_HORIZONTAL_DEGREES:
        label = "Horizontal"
    else:
        label = "Tilted"
    return label, tilt_degrees

def packet_orientation_rotation(packet: TelemetryPacket) -> tuple:
    import numpy as np
    quaternion = np.array([packet.qw, packet.qx, packet.qy, packet.qz], dtype=float)
    if np.all(np.isfinite(quaternion)):
        norm = np.linalg.norm(quaternion)
        if norm > 0.01:
            rotation = quaternion_rotation_matrix(quaternion / norm)
            if np.all(np.isfinite(rotation)):
                return rotation, "quat"
    return np.identity(3), "unknown"

def packet_has_default_orientation(packet: TelemetryPacket) -> bool:
    import numpy as np
    quaternion = np.array([packet.qw, packet.qx, packet.qy, packet.qz], dtype=float)
    if not np.all(np.isfinite(quaternion)):
        return True
    return bool(np.linalg.norm(quaternion - np.array([1.0, 0.0, 0.0, 0.0], dtype=float)) <= ORIENTATION_IDENTITY_EPS)
```

- [ ] Commit: `git commit -m "feat: add orientation math functions"`

### Task 9: Add ApogeeDetector

```python
@dataclass
class ApogeeDetector:
    window_size: int = 5
    descent_threshold: float = -2.0
    alt_history: list = field(default_factory=list)
    apogee_detected: bool = False
    apogee_time: Optional[float] = None
    apogee_altitude: Optional[float] = None
    
    def update(self, packet: TelemetryPacket) -> bool:
        """Return True if apogee just detected."""
        if self.apogee_detected or not math.isfinite(packet.alt_baro):
            return False
        
        self.alt_history.append((packet.arrival_time, packet.alt_baro))
        
        if len(self.alt_history) > self.window_size:
            self.alt_history.pop(0)
        
        if len(self.alt_history) < self.window_size:
            return False
        
        time_span = self.alt_history[-1][0] - self.alt_history[0][0]
        if time_span < 0.1:
            return False
        
        alt_change = self.alt_history[-1][1] - self.alt_history[0][1]
        velocity = alt_change / time_span
        
        if velocity < self.descent_threshold:
            max_alt = max(alt for _, alt in self.alt_history)
            max_idx = [i for i, (_, alt) in enumerate(self.alt_history) if alt == max_alt][0]
            
            self.apogee_detected = True
            self.apogee_time = self.alt_history[max_idx][0]
            self.apogee_altitude = max_alt
            return True
        
        return False
```

- [ ] Add `from dataclasses import field` to imports
- [ ] Commit: `git commit -m "feat: add ApogeeDetector for auto apogee marking"`

### Task 10: Add AlertConfig

```python
@dataclass
class AlertConfig:
    low_voltage_threshold: float = 3.5
    weak_rssi_threshold: int = -110
    stale_packet_seconds: float = 3.0
    malformed_burst_threshold: int = 5
    
    def to_dict(self) -> dict:
        return {
            'low_voltage_threshold': self.low_voltage_threshold,
            'weak_rssi_threshold': self.weak_rssi_threshold,
            'stale_packet_seconds': self.stale_packet_seconds,
            'malformed_burst_threshold': self.malformed_burst_threshold,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'AlertConfig':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
```

- [ ] Commit: `git commit -m "feat: add AlertConfig for configurable alerts"`

### Task 11: Remove dead constants from monitor

- [ ] Delete lines 75-81 in ground_station_monitor.py
- [ ] Commit: `git commit -m "refactor: remove unused orientation constants"`

### Task 12: Fix drag sensitivity

- [ ] Change line 81: `ORIENTATION_VIEW_DRAG_SENSITIVITY = 0.6` → `0.3`
- [ ] Commit: `git commit -m "fix: reduce orientation drag sensitivity"`

### Task 13: Add input validation

In `_connect_port` method around line 1538:

```python
def _connect_port(self, source: str) -> None:
    port = self.port_vars[source].get().strip()
    if not port:
        self.status_vars[source].set("error: empty port")
        self.summary_var.set(f"{source}: port field empty")
        return
    
    try:
        baud = int(self.baud_vars[source].get().strip())
        if not (9600 <= baud <= 921600):
            raise ValueError("out of range")
    except ValueError:
        self.status_vars[source].set("error: invalid baud")
        self.summary_var.set(f"{source}: baud must be 9600-921600")
        return
    
    # ... rest of existing code
```

- [ ] Commit: `git commit -m "feat: add port/baud input validation"`

### Task 14: Update monitor imports

Replace import section in ground_station_monitor.py:

```python
from telemetry_core import (
    AlertConfig,
    ApogeeDetector,
    LogWriter,
    MergeBuffer,
    PortState,
    TelemetryPacket,
    TelemetryParser,
    evaluate_alerts,
    evaluate_port_alerts,
    orientation_label,
    packet_has_default_orientation,
    packet_orientation_rotation,
    quaternion_rotation_matrix,
    rotation_euler_degrees,
)
```

- [ ] Remove now-imported constants from top of monitor file
- [ ] Commit: `git commit -m "refactor: import core classes from telemetry_core"`

### Task 15: Remove extracted code

- [ ] Delete TelemetryPacket class (lines 119-192)
- [ ] Delete PortState class (lines 202-240)
- [ ] Delete evaluate_alerts/evaluate_port_alerts (lines 243-264)
- [ ] Delete TelemetryParser (lines 267-371)
- [ ] Delete MergeBuffer (lines 374-407)
- [ ] Delete LogWriter (lines 410-487)
- [ ] Delete orientation functions (lines 667-725)
- [ ] Commit: `git commit -m "refactor: remove code extracted to telemetry_core"`

### Task 16: Add CSV export feature

In `_build_merge_tab` after Timeline Events frame (~line 1155):

```python
ttk.Button(side, text="Export Merged CSV", command=self._export_merged_csv).pack(fill="x", pady=4)
```

Add method to GroundStationMonitorApp:

```python
def _export_merged_csv(self) -> None:
    from tkinter import filedialog
    if not self.merged_log_packets:
        self.summary_var.set("No data to export")
        return
    
    default_name = f"merged_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    path = filedialog.asksaveasfilename(
        title="Export Merged CSV",
        initialdir=str(DATA_DIR),
        initialfile=default_name,
        defaultextension=".csv",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
    )
    
    if not path:
        return
    
    try:
        with open(path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['time', 'millis', 'lat', 'lon', 'alt_gps', 'sats', 'alt_baro', 'temp', 'pressure',
                           'ax', 'ay', 'az', 'gx', 'gy', 'gz', 'qw', 'qx', 'qy', 'qz',
                           'high_ax', 'high_ay', 'high_az', 'voltage', 'current', 'watt', 'source', 'rssi', 'snr'])
            for packet in self.merged_log_packets:
                rssi = "" if packet.rssi is None else str(packet.rssi)
                snr = "" if packet.snr is None else f"{packet.snr:.2f}"
                row = [datetime.fromtimestamp(packet.arrival_time).isoformat(timespec='seconds')] + packet.csv_values() + [packet.source, rssi, snr]
                writer.writerow(row)
        
        self.summary_var.set(f"Exported {len(self.merged_log_packets)} packets to {Path(path).name}")
    except Exception as exc:
        self.summary_var.set(f"Export failed: {exc}")
```

- [ ] Commit: `git commit -m "feat: add CSV export for merged data"`

### Task 17: Add packet loss display

Add helper function:

```python
def calculate_packet_loss(state: PortState) -> tuple[int, float]:
    """Return (expected_count, loss_percent)."""
    if not state.latest_packet or state.first_packet_millis is None:
        return 0, 0.0
    
    first_millis = state.first_packet_millis
    last_millis = state.latest_packet.millis
    span_seconds = (last_millis - first_millis) / 1000.0
    expected = int(span_seconds * 10)
    
    if expected == 0:
        return 0, 0.0
    
    received = state.packet_count
    loss_percent = max(0.0, (expected - received) / expected * 100)
    return expected, loss_percent
```

Update `_update_port_view` around line 1853:

```python
expected, loss_pct = calculate_packet_loss(state)
if expected > 0:
    packet_line = f"Packets: {state.packet_count} / ~{expected} ({loss_pct:.1f}% loss)  Malformed: {state.malformed_count}"
else:
    packet_line = f"Packets: {state.packet_count}  Malformed: {state.malformed_count}"

detail_var.set(
    f"{source}: {state.status}\n"
    f"{packet_line}\n"
    # ... rest
)
```

- [ ] Commit: `git commit -m "feat: add packet loss percentage display"`

### Task 18: Integrate ApogeeDetector

In `__init__`:

```python
self.apogee_detector = ApogeeDetector()
self.alert_config = AlertConfig()
```

In `_update_merge_view` after line 1918:

```python
if self.apogee_detector.update(packet):
    self._record_event(f"Apogee ({self.apogee_detector.apogee_altitude:.1f}m)")
```

In `_clear_session_data`:

```python
self.apogee_detector = ApogeeDetector()
```

- [ ] Commit: `git commit -m "feat: integrate auto-apogee detection"`

### Task 19: Add alert settings dialog

Add menu button in control row (~line 1076):

```python
ttk.Button(action_row, text="Alert Settings", command=self._show_alert_settings).pack(side="left", padx=2)
```

Add methods:

```python
def _show_alert_settings(self) -> None:
    dialog = tk.Toplevel(self)
    dialog.title("Alert Thresholds")
    dialog.geometry("350x200")
    dialog.transient(self)
    
    entries = {}
    for idx, (key, label, unit) in enumerate([
        ('low_voltage_threshold', 'Low Voltage', 'V'),
        ('weak_rssi_threshold', 'Weak RSSI', 'dBm'),
        ('stale_packet_seconds', 'Stale Packet', 's'),
        ('malformed_burst_threshold', 'Malformed Burst', 'count'),
    ]):
        ttk.Label(dialog, text=f"{label} ({unit}):").grid(row=idx, column=0, sticky='e', padx=10, pady=5)
        entry = ttk.Entry(dialog, width=12)
        entry.insert(0, str(getattr(self.alert_config, key)))
        entry.grid(row=idx, column=1, sticky='w', padx=10, pady=5)
        entries[key] = entry
    
    def apply():
        try:
            self.alert_config = AlertConfig(
                low_voltage_threshold=float(entries['low_voltage_threshold'].get()),
                weak_rssi_threshold=int(entries['weak_rssi_threshold'].get()),
                stale_packet_seconds=float(entries['stale_packet_seconds'].get()),
                malformed_burst_threshold=int(entries['malformed_burst_threshold'].get()),
            )
            self._save_alert_config()
            dialog.destroy()
            self.summary_var.set("Alert thresholds updated")
        except ValueError as exc:
            self.summary_var.set(f"Invalid alert config: {exc}")
    
    def reset():
        self.alert_config = AlertConfig()
        dialog.destroy()
        self._save_alert_config()
        self.summary_var.set("Alert thresholds reset to defaults")
    
    ttk.Button(dialog, text="Apply", command=apply).grid(row=4, column=0, padx=10, pady=10)
    ttk.Button(dialog, text="Reset Defaults", command=reset).grid(row=4, column=1, padx=10, pady=10)

def _save_alert_config(self) -> None:
    import json
    config_path = Path.home() / '.duck2dragon_alerts.json'
    try:
        with open(config_path, 'w') as f:
            json.dump(self.alert_config.to_dict(), f, indent=2)
    except Exception:
        pass

def _load_alert_config(self) -> AlertConfig:
    import json
    config_path = Path.home() / '.duck2dragon_alerts.json'
    try:
        with open(config_path, 'r') as f:
            return AlertConfig.from_dict(json.load(f))
    except Exception:
        return AlertConfig()
```

In `__init__`:

```python
self.alert_config = self._load_alert_config()
```

Update `evaluate_alerts`/`evaluate_port_alerts` calls to pass `config=self.alert_config`.

- [ ] Commit: `git commit -m "feat: add configurable alert settings with persistence"`

### Task 20: Testing

- [ ] Run `python3 Data/ground_station_monitor.py` - verify no import errors
- [ ] Test GPS validation: Create test packet with (0,0) coords, 5 sats - should be valid
- [ ] Test drag sensitivity: Rotate overlay, verify smooth not wild
- [ ] Test input validation: Enter "abc" baud, verify error shown
- [ ] Test export: Export merged CSV, open in spreadsheet, verify 28 columns
- [ ] Test packet loss: Replay known log, verify loss % reasonable
- [ ] Test apogee: Replay log with known apogee, verify auto-event fires
- [ ] Test alert config: Change voltage to 3.3V, verify triggers at 3.2V
- [ ] Run full regression checklist from spec
- [ ] Hardware test: Connect both serial ports, verify merge works
- [ ] Commit: `git commit -m "test: verify all features and regression tests pass"`

---

## Execution Complete

Plan saved. Ready for implementation via subagent-driven-development or executing-plans skill.

