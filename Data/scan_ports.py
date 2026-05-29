#!/usr/bin/env python3
"""
scan_ports.py — List all available serial ports on this machine.
Usage: python3 scan_ports.py
"""
import sys

try:
    import serial.tools.list_ports
except ImportError:
    print("pyserial not installed. Run: pip install pyserial")
    sys.exit(1)

ports = list(serial.tools.list_ports.comports())

if not ports:
    print("No serial ports found.")
    sys.exit(0)

print(f"Found {len(ports)} port(s):\n")
for p in sorted(ports):
    print(f"  {p.device:<20} | {p.description}")
    if p.hwid and p.hwid != "n/a":
        print(f"  {'':20} | HWID: {p.hwid}")
