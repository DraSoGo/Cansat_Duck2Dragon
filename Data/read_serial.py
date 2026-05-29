#!/usr/bin/env python3
"""
CANSAT Duck2Dragon — Ground Station Serial Logger.

Reads incoming CSV telemetry from the Ground Station's USB serial port
and appends each line verbatim to Data/log.txt.

CSV fields (23):
  millis,lat,lon,alt_gps,sats,alt_baro,temp,pressure,
  ax,ay,az,gx,gy,gz,qw,qx,qy,qz,
  high_ax,high_ay,high_az,voltage,current

Usage:
    python3 read_serial.py                       # default /dev/ttyUSB0
    python3 read_serial.py /dev/ttyUSB0
    python3 read_serial.py /dev/ttyUSB0 115200   # custom baud
"""
import sys
import os
import signal
from datetime import datetime

import serial


DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 115200
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log.txt")

CSV_HEADER = (
    "millis,lat,lon,alt_gps,sats,alt_baro,temp,pressure,"
    "ax,ay,az,gx,gy,gz,qw,qx,qy,qz,"
    "high_ax,high_ay,high_az,voltage,current"
)
CSV_FIELDS = 23


def parse_csv(line: str) -> bool:
    """Return True if line looks like a valid telemetry CSV row."""
    if line.startswith("#"):
        return False
    parts = line.split(",")
    return len(parts) == CSV_FIELDS


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PORT
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_BAUD

    print(f"[read_serial] port={port} baud={baud} log={LOG_PATH}")
    print(f"[read_serial] expecting {CSV_FIELDS}-field CSV: {CSV_HEADER}")

    try:
        ser = serial.Serial(port, baud, timeout=1)
    except serial.SerialException as e:
        print(f"[read_serial] FAIL to open {port}: {e}", file=sys.stderr)
        return 1

    # Graceful Ctrl+C
    def _stop(signum, frame):
        print("\n[read_serial] stopping")
        try:
            ser.close()
        except Exception:
            pass
        sys.exit(0)
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    with open(LOG_PATH, "a", buffering=1) as f:
        # Session marker + header
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        marker = f"# session start {ts}"
        print(marker)
        f.write(marker + "\n")
        f.write(f"# {CSV_HEADER}\n")

        rx_count = 0
        err_count = 0

        while True:
            try:
                raw = ser.readline()
            except serial.SerialException as e:
                print(f"[read_serial] serial error: {e}", file=sys.stderr)
                break

            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue

            # Always log everything (comments + data)
            f.write(line + "\n")

            if line.startswith("#"):
                # Status / RSSI / SNR lines from ground station
                print(f"\033[90m{line}\033[0m")  # grey
                continue

            if parse_csv(line):
                rx_count += 1
                parts = line.split(",")
                # Quick live display: millis, alt_baro, temp, sats, RSSI handled separately
                try:
                    ms    = int(parts[0])
                    alt   = float(parts[5])
                    temp  = float(parts[6])
                    sats  = int(parts[4])
                    volt  = float(parts[21])
                    print(f"[{ms:>10}ms] alt={alt:>8.2f}m  T={temp:>6.2f}°C  sats={sats}  V={volt:.3f}  #{rx_count}")
                except (ValueError, IndexError):
                    print(line)
            else:
                err_count += 1
                print(f"[read_serial] malformed ({len(line.split(','))} fields): {line[:80]}", file=sys.stderr)

    print(f"[read_serial] done. rx={rx_count} err={err_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
