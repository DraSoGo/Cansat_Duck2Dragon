# Duck2Dragon Ground Station Monitor Design

Date: 2026-06-05
Status: Approved design

## Context

The Duck2Dragon CanSat ground station currently uses CLI serial loggers in `Data/read_serial_1.py` and `Data/read_serial_2.py`. Each logger reads one USB serial port, prints received telemetry, validates CSV field count, tracks RSSI/SNR comment lines, and appends raw data to a log file.

Current firmware in `Rocket/cansat/cansat.ino` emits 24-field telemetry packets including `watt`. `Rocket/ground_station/ground_station.ino` forwards LoRa packets to USB serial and prints RSSI/SNR as comment lines.

The new monitor will provide a Tkinter-based ground station dashboard for two LoRa receivers while keeping the existing CLI loggers as fallback tools.

## Goals

- Build a new GUI app titled `Duck2Dragon Monitor`.
- Read two serial ports concurrently for two LoRa receiver boards.
- Show three tabs: `Merge Data`, `Port 1`, and `Port 2`.
- Treat both serial ports as redundant receivers of the same CanSat telemetry.
- Merge duplicate packets by `millis`, selecting the packet with the highest RSSI.
- Save raw Port 1, raw Port 2, and merged logs.
- Provide a full dashboard with charts, offline GPS track, flight timeline, alerts, and replay mode.
- Keep `Data/read_serial_1.py` and `Data/read_serial_2.py` unchanged as CLI fallbacks.

## Non-Goals

- Do not replace `Data/read_serial_1.py` in the first GUI version.
- Do not modify Arduino firmware.
- Do not add online map tile dependencies.
- Do not add editable alert thresholds in the first version.
- Do not require internet access during launch-day use.

## File Arrangement

Create a new runnable GUI file:

```text
Data/ground_station_monitor.py
```

Keep existing CLI files available:

```text
Data/read_serial_1.py
Data/read_serial_2.py
```

This intentionally updates the original replacement idea: `read_serial_1.py` will not be replaced because the approved design keeps a CLI fallback.

## Dependencies

Required:

- Python 3
- Tkinter from the standard Python installation
- `pyserial`
- `matplotlib`

Install command:

```bash
python3 -m pip install pyserial matplotlib
```

## Telemetry Schema

The monitor treats the current 24-field firmware schema as official:

```text
millis,lat,lon,alt_gps,sats,alt_baro,temp,pressure,ax,ay,az,gx,gy,gz,qw,qx,qy,qz,high_ax,high_ay,high_az,voltage,current,watt
```

Rows with a different field count are malformed for this version.

Comment/status lines beginning with `#` are not telemetry rows, but RSSI/SNR comments are parsed and associated with the current port.

## Data Flow

1. The user selects Port 1 and Port 2 from dropdowns, with default values prefilled.
2. Each port reader opens its serial port at the selected baud rate.
3. Background reader threads decode serial lines and push events into a queue.
4. The Tkinter main loop consumes queued events.
5. Raw lines are written to per-port logs.
6. Valid telemetry rows are parsed into typed packet records.
7. RSSI/SNR comment lines update the latest link metadata for the source port.
8. Parsed packets update the source port tab.
9. Parsed packets enter the merge buffer keyed by `millis`.
10. The merge logic selects the best packet for each `millis`.
11. Merged packets update the Merge Data tab and merged log.

GUI updates happen only on the Tkinter main thread.

## Merge Rules

Packet identity:

- `millis`

Duplicate packet selection:

- If both packets have RSSI, choose the packet with the higher RSSI value.
- Example: `-55 dBm` beats `-80 dBm`.
- If only one packet has RSSI, choose the packet with RSSI.
- If neither packet has RSSI, keep the first received packet.

Raw packets remain in their per-port logs even when they lose the merge decision.

Merged rows include source port and selected RSSI/SNR metadata.

## UI Layout

The window title is:

```text
Duck2Dragon Monitor
```

The top control bar contains:

- Port 1 dropdown, baud entry, connect/disconnect button, and status indicator.
- Port 2 dropdown, baud entry, connect/disconnect button, and status indicator.
- Refresh ports button.
- Start/stop logging button.
- Replay log button.
- Session status summary: elapsed time, packet count, merged count, malformed count.

Defaults:

- Baud: `115200`
- Port 1: `/dev/ttyACM0`
- Port 2: `/dev/ttyUSB0`

Detected serial ports can override the defaults through dropdowns.

## Merge Data Tab

The approved layout direction is Map First.

This tab prioritizes recovery/location while still showing flight and link status:

- Offline GPS track plot from latitude/longitude.
- Latest coordinate display.
- Satellite count and GPS lock state.
- Altitude chart.
- Link quality chart for Port 1 and Port 2 RSSI/SNR.
- Battery and power readouts: voltage, current, watt.
- Flight timeline.
- Manual event buttons: Launch, Apogee, Deployment, Landing.
- Recent merged packet table with source port.

The GPS plot is local and offline. It should plot relative track movement from received latitude/longitude values and handle missing or zero GPS data gracefully.

## Port Tabs

`Port 1` and `Port 2` tabs share the same structure:

- Connection state: connected, reconnecting, offline, or replay.
- Latest raw line.
- Latest parsed telemetry summary.
- RSSI/SNR display.
- Packet count and malformed count.
- Raw packet table.
- Per-port charts for altitude, voltage, and RSSI/SNR.
- Log file path/status.

## Logging

Each GUI session writes organized logs under:

```text
Data/logs/
```

Recommended session filename pattern:

```text
YYYY-MM-DD_HH-MM-SS_port1.csv
YYYY-MM-DD_HH-MM-SS_port2.csv
YYYY-MM-DD_HH-MM-SS_merged.csv
YYYY-MM-DD_HH-MM-SS_events.csv
```

Each raw per-port telemetry log includes:

- Session start metadata.
- CSV header.
- Every non-empty raw line received from that port, including valid telemetry, comments, and malformed lines.
- Relevant parsed status metadata where useful, especially RSSI/SNR.

The merged log includes only selected valid telemetry packets, plus additional metadata fields for source port and selected RSSI/SNR.

Log writes should be line-buffered to reduce data loss if the app exits unexpectedly.

## Replay Mode

Replay mode supports dashboard testing without serial hardware.

Replay behavior:

- User selects an existing log file.
- Rows are fed through the same parser, merge logic, charts, tables, and alerts as live serial data.
- Controls include start, pause, stop, and playback speed.
- Replay mode should not require connected serial ports.

## Alerts

The first version uses fixed warning thresholds. Alerts are visible status colors/text, not disruptive pop-up dialogs.

Alert categories:

- Low voltage.
- Weak RSSI.
- Packet timeout or stale stream.
- No GPS lock or zero satellites.
- Burst of malformed packets.

Exact numeric thresholds can be defined during implementation, but they should be conservative and easy to find in code.

## Serial Reliability

Each serial reader should:

- Run in a background thread.
- Avoid blocking the Tkinter event loop.
- Decode bytes as UTF-8 with replacement for invalid bytes.
- Continue operating if the other port fails.
- Auto-reconnect when a port disconnects.
- Show reconnecting/offline state in the UI.

Auto-reconnect should be clear to the user; it must not silently hide connection problems.

## Implementation Notes

Recommended internal components:

- `TelemetryPacket`: parsed 24-field telemetry record.
- `PortState`: per-port connection, counters, latest RSSI/SNR, and latest packet.
- `SerialReader`: background worker for one serial port.
- `ReplayReader`: replay worker that emits the same queue events as serial readers.
- `TelemetryParser`: CSV and RSSI/SNR parsing.
- `MergeBuffer`: duplicate resolution by `millis` and highest RSSI.
- `LogWriter`: line-buffered per-port and merged logging.
- `GroundStationMonitorApp`: Tkinter UI and main-thread event handling.

Charts should use matplotlib embedded in Tkinter.

## Success Criteria

- The GUI starts from `Data/ground_station_monitor.py`.
- The title displays `Duck2Dragon Monitor`.
- Port 1 and Port 2 can be selected from defaults or scanned serial ports.
- The app can read both serial ports concurrently.
- Port tabs show raw and parsed data independently.
- Merge Data tab deduplicates by `millis` and chooses highest RSSI.
- Raw Port 1, raw Port 2, merged, and event logs are saved.
- Replay mode can drive the dashboard from an existing log.
- Serial disconnect on one port does not stop the whole app.
- Existing CLI loggers remain available unchanged.
