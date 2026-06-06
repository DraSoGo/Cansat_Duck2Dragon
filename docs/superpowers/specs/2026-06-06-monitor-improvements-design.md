# Ground Station Monitor Improvements

**Date:** 2026-06-06  
**Status:** Approved  
**Approach:** Hybrid refactor - bug fixes + critical features + partial code extraction

## Context

`ground_station_monitor.py` is a 2187-line Tkinter GUI for dual-LoRa ground station telemetry. Works well but has quality issues, missing competition-critical features, and bugs. Competition deadline approaching - need fast, low-risk improvements.

## Goals

1. **Fix bugs:** GPS validation, orientation drag sensitivity, input validation
2. **Add competition features:** CSV export, packet loss metrics, auto-apogee detection, configurable alerts
3. **Improve maintainability:** Extract core logic classes from GUI monolith
4. **Preserve stability:** No GUI behavior changes, keep existing test workflows

## Design

### 1. Bug Fixes

#### GPS Validation (line 152-160)
**Problem:** Current logic rejects valid (0.0, 0.0) coordinates (Gulf of Guinea).

**Current:**
```python
def gps_valid(self) -> bool:
    return (
        self.sats > 0
        and math.isfinite(self.lat)
        and math.isfinite(self.lon)
        and -WEB_MERCATOR_MAX_LAT <= self.lat <= WEB_MERCATOR_MAX_LAT
        and MIN_LON <= self.lon <= MAX_LON
        and not (self.lat == 0.0 and self.lon == 0.0)  # <-- WRONG
    )
```

**Fixed:**
```python
def gps_valid(self) -> bool:
    return (
        self.sats > 0
        and math.isfinite(self.lat)
        and math.isfinite(self.lon)
        and -WEB_MERCATOR_MAX_LAT <= self.lat <= WEB_MERCATOR_MAX_LAT
        and MIN_LON <= self.lon <= MAX_LON
    )
```
Reject only when GPS clearly hasn't locked. Allow (0,0) if sats > 0.

#### Orientation Drag Sensitivity (line 81)
Change `ORIENTATION_VIEW_DRAG_SENSITIVITY = 0.6` → `0.3`. Current value too high, model spins wildly.

#### Input Validation
Add to `_connect_port()` method:
- **Port validation:** Check non-empty, warn if not in detected ports list
- **Baud validation:** Range check 9600-921600, default to 115200 on invalid

#### Dead Code Removal
Remove unused constants (lines 75-81):
```python
ORIENTATION_MODEL_VIEW_LEFT = 0.26
ORIENTATION_MODEL_VIEW_BOTTOM = 0.04
ORIENTATION_MODEL_VIEW_WIDTH = 0.50
ORIENTATION_MODEL_VIEW_HEIGHT = 0.55
ORIENTATION_VIEW_ELEV = 20.0
ORIENTATION_VIEW_AZIM = -45.0
ORIENTATION_VIEW_DRAG_SENSITIVITY = 0.6
```
These were used before orientation overlay refactor (commit b55e0bf), now defined inline at lines 1207-1215.

---

### 2. New Features

#### A. CSV Export
**Where:** Add "Export Merged CSV" button in merge tab side panel (after Timeline Events frame)

**Functionality:**
- Opens file dialog, default name: `merged_export_{timestamp}.csv`
- Writes header: `time,millis,lat,lon,alt_gps,sats,alt_baro,temp,pressure,ax,ay,az,gx,gy,gz,qw,qx,qy,qz,high_ax,high_ay,high_az,voltage,current,watt,source,rssi,snr`
- Exports all packets in `self.merged_log_packets` (full session history)
- Shows status: "Exported N packets to {path}"

#### B. Packet Loss Metrics
**Where:** Add to each port's status display (line 1843-1862)

**Calculation:**
```python
def calculate_packet_loss(state: PortState) -> tuple[int, float]:
    """Return (expected_count, loss_percent)."""
    if not state.latest_packet:
        return 0, 0.0
    
    # Estimate expected packets from millis span and typical rate (~10 Hz)
    first_millis = state.first_packet_millis  # track this
    last_millis = state.latest_packet.millis
    span_seconds = (last_millis - first_millis) / 1000.0
    expected = int(span_seconds * 10)  # assume 10 Hz nominal rate
    
    if expected == 0:
        return 0, 0.0
    
    received = state.packet_count
    loss_percent = max(0.0, (expected - received) / expected * 100)
    return expected, loss_percent
```

**Display:** Add line to port detail view:
```
Packets: 1234 / ~1450 (14.9% loss)
```

#### C. Auto-Apogee Detection
**Where:** Add `ApogeeDetector` class to `telemetry_core.py`

**Algorithm:**
```python
class ApogeeDetector:
    def __init__(self, window_size: int = 5, descent_threshold: float = -2.0):
        self.window_size = window_size
        self.descent_threshold = descent_threshold  # m/s
        self.alt_history: list[tuple[float, float]] = []  # (time, altitude)
        self.apogee_detected = False
        self.apogee_time: Optional[float] = None
        self.apogee_altitude: Optional[float] = None
    
    def update(self, packet: TelemetryPacket) -> bool:
        """Return True if apogee just detected."""
        if self.apogee_detected or not math.isfinite(packet.alt_baro):
            return False
        
        self.alt_history.append((packet.arrival_time, packet.alt_baro))
        
        # Keep only recent window
        if len(self.alt_history) > self.window_size:
            self.alt_history.pop(0)
        
        if len(self.alt_history) < self.window_size:
            return False
        
        # Check if descending consistently
        time_span = self.alt_history[-1][0] - self.alt_history[0][0]
        if time_span < 0.1:
            return False
        
        alt_change = self.alt_history[-1][1] - self.alt_history[0][1]
        velocity = alt_change / time_span
        
        if velocity < self.descent_threshold:
            # Find max altitude in history
            max_alt = max(alt for _, alt in self.alt_history)
            max_idx = [i for i, (_, alt) in enumerate(self.alt_history) if alt == max_alt][0]
            
            self.apogee_detected = True
            self.apogee_time = self.alt_history[max_idx][0]
            self.apogee_altitude = max_alt
            return True
        
        return False
```

**Integration:**
- Add `self.apogee_detector = ApogeeDetector()` to `GroundStationMonitorApp.__init__`
- Call `self.apogee_detector.update(packet)` in `_update_merge_view()`
- If returns True, call `self._record_event("Apogee")` automatically
- Display apogee altitude in merge status bar after detection

#### D. Configurable Alerts
**Where:** Add settings dialog (Menu → Settings or button in control row)

**Configurable thresholds:**
```python
@dataclass
class AlertConfig:
    low_voltage_threshold: float = 3.5  # V
    weak_rssi_threshold: int = -110     # dBm
    stale_packet_seconds: float = 3.0   # s
    malformed_burst_threshold: int = 5
```

**Dialog:**
- Simple ttk.Toplevel with 4 labeled entry fields
- "Apply" button updates `self.alert_config`
- "Reset Defaults" button
- Persist to `~/.duck2dragon_alerts.json` on apply

**Usage:** Pass `alert_config` to `evaluate_alerts()` and `evaluate_port_alerts()`.

---

### 3. Code Extraction

Create `Data/telemetry_core.py` with:

#### Classes to Extract
1. **`TelemetryPacket`** (dataclass, lines 119-192)
2. **`PortState`** (dataclass, lines 202-240)
3. **`TelemetryParser`** (class, lines 267-371)
4. **`MergeBuffer`** (class, lines 374-407)
5. **`LogWriter`** (class, lines 410-487)
6. **`ApogeeDetector`** (new class, see above)

#### Functions to Extract
- `evaluate_alerts()` (lines 243-257)
- `evaluate_port_alerts()` (lines 260-264)
- `packet_orientation_axis()` (lines 667-669)
- `packet_orientation_rotation()` (lines 672-681)
- `packet_has_default_orientation()` (lines 684-688)
- `quaternion_rotation_matrix()` (lines 691-700)
- `rotation_euler_degrees()` (lines 703-713)
- `orientation_label()` (lines 716-725)

#### What Stays in `ground_station_monitor.py`
- All GUI classes: `GroundStationMonitorApp`, `ReplayReader`, `SerialReader`, `SerialEventQueue`
- OSM tile utilities (GUI-specific)
- Helper functions: `configure_window_icon()`, `list_serial_ports()`, `default_serial_factory()`

#### Import Pattern
```python
# In ground_station_monitor.py
from telemetry_core import (
    TelemetryPacket,
    PortState,
    TelemetryParser,
    MergeBuffer,
    LogWriter,
    ApogeeDetector,
    evaluate_alerts,
    evaluate_port_alerts,
    packet_orientation_rotation,
    quaternion_rotation_matrix,
    rotation_euler_degrees,
    orientation_label,
)
```

---

### 4. File Structure After Refactor

```
Data/
├── telemetry_core.py          # NEW - core logic (450 lines)
├── ground_station_monitor.py  # GUI only (1600 lines, down from 2187)
├── read_serial_1.py
├── read_serial_2.py
└── logs/
```

**Benefits:**
- `telemetry_core.py` testable in isolation
- Parser/merge logic reusable for offline analysis tools
- Main file drops ~600 lines, easier to navigate
- No GUI behavior changes

---

## Verification Plan

### Bug Fixes
1. **GPS validation:** Mock packet with (0, 0, sats=5), verify `gps_valid == True`
2. **Drag sensitivity:** Manually test orientation overlay, verify smooth rotation
3. **Input validation:** Enter invalid baud "abc", verify error message shown

### New Features
1. **Export:** Export merged data, verify CSV opens in Excel with correct 28 columns
2. **Packet loss:** Run replay with known packet sequence, verify loss % matches expected
3. **Apogee detection:** 
   - Replay log with known apogee at T+60s, alt=419m
   - Verify auto-event within 2s window
   - Check no false positives during ascent
4. **Configurable alerts:** 
   - Set voltage threshold to 3.3V, verify alert triggers at 3.2V
   - Persist settings, restart app, verify settings loaded

### Code Extraction
1. **Import check:** Run `python3 ground_station_monitor.py`, verify no import errors
2. **Unit tests:** Create `test_telemetry_core.py`:
   ```python
   def test_parser_24_fields():
       line = "100,0,0,0,0,50.2,25.1,1013.2,0,0,9.8,0,0,0,1,0,0,0,0,0,0,3.7,120,0.444"
       packet = TelemetryParser.parse_packet(line, "port1", -90, 10.5)
       assert packet.millis == 100
       assert packet.alt_baro == 50.2
       assert packet.voltage == 3.7
   ```
3. **Integration test:** Connect to hardware, verify dual-port merge still works

### Regression Testing
- [ ] Connect Port 1, verify status updates
- [ ] Connect Port 2, verify status updates  
- [ ] Verify merged packets appear in Merge tab
- [ ] Verify GPS map updates with valid coordinates
- [ ] Verify orientation overlay updates
- [ ] Verify altitude/RSSI charts update
- [ ] Record timeline event, verify appears in log
- [ ] Replay log file, verify playback works
- [ ] Toggle dark mode, verify theme applies
- [ ] Reset session, verify all data clears

---

## Implementation Order

1. **Phase 1: Bug fixes** (30 min)
   - GPS validation fix
   - Drag sensitivity change
   - Input validation
   - Dead code removal

2. **Phase 2: Code extraction** (60 min)
   - Create `telemetry_core.py`
   - Move classes/functions
   - Update imports
   - Test import

3. **Phase 3: New features** (90 min)
   - Export button + functionality
   - Packet loss calculation + display
   - ApogeeDetector class + integration
   - Alert config dialog + persistence

4. **Phase 4: Testing** (30 min)
   - Run verification plan
   - Fix any issues found
   - Hardware integration test

**Total estimated time:** 3.5 hours

---

## Post-Competition TODOs

After competition, consider full modular refactor:
- Extract GUI tabs to separate files: `gui/merge_tab.py`, `gui/port_tab.py`
- Extract readers to `serial_io.py`
- Extract GPS/OSM utilities to `gps_utils.py`
- Add comprehensive unit tests
- Add CLI mode for headless logging

---

## Success Criteria

- [ ] All 4 bugs fixed, verified
- [ ] All 4 new features working, verified
- [ ] Code extracted to `telemetry_core.py`, imports clean
- [ ] All regression tests pass
- [ ] File size reduced to ~1600 lines (27% reduction)
- [ ] No GUI behavior changes
- [ ] Competition-ready within 3.5 hours
