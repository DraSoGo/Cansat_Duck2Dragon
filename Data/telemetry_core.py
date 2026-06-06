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
