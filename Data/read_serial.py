#!/usr/bin/env python3
"""
CANSAT Duck2Dragon — Ground Station Serial Logger.

Reads incoming CSV telemetry from the Ground Station's USB serial port
and appends each line verbatim to Data/log.txt.

Usage:
    python3 read_serial.py                       # default /dev/ttyACM0
    python3 read_serial.py /dev/ttyUSB0
    python3 read_serial.py /dev/ttyUSB0 9600     # custom baud
"""
import sys
import os
import signal
from datetime import datetime

import serial


DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_BAUD = 115200
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log.txt")


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PORT
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_BAUD

    print(f"[read_serial] port={port} baud={baud} log={LOG_PATH}")

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
        # Session marker
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        marker = f"# session start {ts}"
        print(marker)
        f.write(marker + "\n")

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

            print(line)
            f.write(line + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
