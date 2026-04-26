# CANSAT Duck2Dragon

Model rocket CanSat project — telemetry, deployment, and ground-link codebase.

The CanSat ascends inside a model rocket to ~419 m apogee, is ejected via a piston-eject system, then descends under its own parachute while logging sensor data to SD and broadcasting it live over LoRa to a ground station.

---

## System architecture

```
   [ Rocket airframe ]                 [ Ground ]
          |
   ┌──────┴───────┐
   |              |
   | CanSat (TTGO ESP32)             Ground Station (TTGO ESP32)
   |   - Sensors                          - LoRa RX
   |   - LoRa TX  ────────── 922.525 MHz ──────────►  Serial USB
   |   - SD log                                          │
   |                                                     ▼
   | Deployment (Arduino Nano)               PC: read_serial.py
   |   - MS5611 apogee detection                         │
   |   - Servo parachute release                         ▼
   |                                                Data/log.txt
```

---

## Repository layout

```
CANSAT_Duck2Dragon/
├── Rocket/
│   ├── cansat.ino           Main flight computer (TTGO ESP32)
│   ├── deployment.ino       Apogee detection + servo release (Arduino Nano)
│   ├── ground_station.ino   LoRa receiver -> USB serial bridge (TTGO ESP32)
│   └── gyro.ino             (autogyro module — not implemented)
├── Module_Test/             Standalone calibration sketches per component
│   ├── adxl375.ino
│   ├── bno055.ino
│   ├── ds3231.ino
│   ├── gps.ino
│   ├── hc-020k.ino
│   ├── ina219.ino
│   ├── lora.ino
│   ├── ms5611.ino
│   ├── sd_card.ino
│   └── servo.ino
├── Data/
│   ├── read_serial.py       PC-side logger (pyserial)
│   ├── log.txt              Telemetry log output
│   └── data_analysis.ipynb  (analysis notebook — not in scope)
├── Document/
│   └── Duck2Dragon.pdf      Project requirements
├── README.md
└── LICENSE
```

---

## Hardware

### CanSat board — TTGO SX1276 LoRa32 (ESP32, 920 MHz region)

| Component                | Interface | Notes                            |
| ------------------------ | --------- | -------------------------------- |
| FPC flexible antenna     | RF        | 920 MHz, +5 dBi                  |
| LoRa SX1276 (built-in)   | SPI/VSPI  | 922.525 MHz, BW=125 kHz, SF=9    |
| GPS NEO-6M               | UART1     | 9600 baud                        |
| BNO055 (9-DOF IMU)       | I2C 0x28  | Quaternion + accel + gyro fusion |
| ADXL375 (high-G accel)   | I2C 0x53  | ±200 g                           |
| MS5611 (barometer)       | I2C 0x77  | Altitude / temperature           |
| INA219 (power monitor)   | I2C 0x40  | Battery V / current              |
| microSD card             | SPI/HSPI  | Telemetry log                    |

### Deployment board — Arduino Nano

| Component | Interface | Notes                                  |
| --------- | --------- | -------------------------------------- |
| MS5611    | I2C       | Barometric apogee detection            |
| Servo     | PWM (D9)  | Parachute release piston eject         |

### Ground Station — TTGO SX1276 LoRa32 (ESP32)

| Component | Notes                                  |
| --------- | -------------------------------------- |
| LoRa      | RX only, prints CSV to USB Serial      |

---

## Pin assignments

### CanSat (ESP32)

| Function     | GPIO              |
| ------------ | ----------------- |
| LoRa SCK     | 5                 |
| LoRa MISO    | 19                |
| LoRa MOSI    | 27                |
| LoRa CS      | 18                |
| LoRa RST     | 14                |
| LoRa DIO0    | 26                |
| I2C SDA      | 21                |
| I2C SCL      | 22                |
| GPS RX       | 16                |
| GPS TX       | 17                |
| SD CS        | 13 (HSPI)         |
| SD MOSI      | 15 (HSPI)         |
| SD MISO      | 2  (HSPI)         |
| SD CLK       | 4  (HSPI)         |

LoRa runs on the default VSPI bus; the SD card runs on a separate `SPIClass(HSPI)` instance to prevent bus contention during transmit.

### Deployment (Arduino Nano)

| Function     | Pin          |
| ------------ | ------------ |
| Servo PWM    | D9           |
| I2C SDA      | A4           |
| I2C SCL      | A5           |
| Status LED   | D13          |

### Ground Station (ESP32)

Same LoRa pins as CanSat, except `LORA_RST = GPIO 23`.

---

## Telemetry CSV format

Each LoRa packet and SD log line is one comma-separated record terminated with `\n`. All packets share the same fixed 23-field schema.

```
millis,lat,lon,alt_gps,sats,alt_baro,temp,pressure,ax,ay,az,gx,gy,gz,qw,qx,qy,qz,high_ax,high_ay,high_az,voltage,current
```

| Idx | Field      | Unit  | Source        |
| --- | ---------- | ----- | ------------- |
| 0   | millis     | ms    | `millis()`    |
| 1   | lat        | deg   | GPS NEO-6M    |
| 2   | lon        | deg   | GPS NEO-6M    |
| 3   | alt_gps    | m     | GPS NEO-6M    |
| 4   | sats       | count | GPS NEO-6M    |
| 5   | alt_baro   | m     | MS5611        |
| 6   | temp       | C     | MS5611        |
| 7   | pressure   | hPa   | MS5611        |
| 8   | ax         | m/s²  | BNO055        |
| 9   | ay         | m/s²  | BNO055        |
| 10  | az         | m/s²  | BNO055        |
| 11  | gx         | deg/s | BNO055        |
| 12  | gy         | deg/s | BNO055        |
| 13  | gz         | deg/s | BNO055        |
| 14  | qw         | -     | BNO055 quat   |
| 15  | qx         | -     | BNO055 quat   |
| 16  | qy         | -     | BNO055 quat   |
| 17  | qz         | -     | BNO055 quat   |
| 18  | high_ax    | g     | ADXL375       |
| 19  | high_ay    | g     | ADXL375       |
| 20  | high_az    | g     | ADXL375       |
| 21  | voltage    | V     | INA219 bus V  |
| 22  | current    | mA    | INA219        |

Lines starting with `#` are metadata (boot markers, RSSI/SNR, session timestamps) and may be discarded by analysis tools.

---

## Required Arduino libraries

Install via the Arduino IDE Library Manager:

| Library                  | Author          | Used by                           |
| ------------------------ | --------------- | --------------------------------- |
| LoRa                     | Sandeep Mistry  | cansat, ground_station, lora test |
| TinyGPSPlus              | mikalhart       | cansat, gps test                  |
| Adafruit BNO055          | Adafruit        | cansat, bno055 test               |
| Adafruit Unified Sensor  | Adafruit        | (BNO055 / ADXL375 dependency)     |
| Adafruit ADXL375         | Adafruit        | cansat, adxl375 test              |
| Adafruit INA219          | Adafruit        | cansat, ina219 test               |
| MS5611                   | Rob Tillaart    | cansat, deployment, ms5611 test   |
| RTClib                   | Adafruit        | ds3231 test                       |

ESP32 board support: install **esp32 by Espressif** (Boards Manager). Select board "TTGO LoRa32-OLED V1" or compatible SX1276 variant.

For the Arduino Nano deployment board, the standard `Wire.h` and `Servo.h` libraries are bundled with the IDE.

---

## Build & upload

### CanSat (TTGO ESP32)
1. Install required libraries (see table above).
2. Open `Rocket/cansat.ino` in the Arduino IDE.
3. **Tools → Board** → "TTGO LoRa32-OLED V1" (or your SX1276 board).
4. **Tools → Port** → select the USB port for the TTGO board.
5. Click **Upload**.
6. Open Serial Monitor at **115200 baud** to verify all sensors report `OK`.

### Deployment (Arduino Nano)
1. Open `Rocket/deployment.ino`.
2. **Tools → Board** → "Arduino Nano" (Processor: ATmega328P).
3. Select port, click **Upload**.
4. Serial Monitor at **9600 baud** to watch state machine.
5. Tune `SERVO_LOCKED_ANGLE` and `SERVO_DEPLOY_ANGLE` `#defines` to match the piston-eject mechanical assembly.

### Ground Station (second TTGO ESP32)
1. Open `Rocket/ground_station.ino`.
2. Same board settings as CanSat.
3. Upload, confirm `# Ground Station ready` at 115200 baud.

---

## Running the data logger

```bash
cd Data
python3 -m pip install pyserial
python3 read_serial.py                       # default /dev/ttyACM0 @ 115200
python3 read_serial.py /dev/ttyUSB0          # custom port
python3 read_serial.py /dev/ttyUSB0 9600     # custom port + baud
```

All received lines are appended to `Data/log.txt`. Press `Ctrl+C` to stop.

---

## Module-level testing

Each file in `Module_Test/` is a self-contained Arduino sketch that exercises **one** sensor or peripheral. Use these for bring-up and troubleshooting before flashing the full `cansat.ino`.

| Test            | Target board   | Verifies                             |
| --------------- | -------------- | ------------------------------------ |
| `ms5611.ino`    | TTGO ESP32     | Pressure / temp / altitude readout   |
| `bno055.ino`    | TTGO ESP32     | Euler / accel / gyro / quat / calib  |
| `adxl375.ino`   | TTGO ESP32     | High-G XYZ in g units                |
| `ina219.ino`    | TTGO ESP32     | Bus V, shunt mV, mA, mW              |
| `ds3231.ino`    | TTGO ESP32     | RTC date/time + temperature          |
| `gps.ino`       | TTGO ESP32     | Lat/lon/altitude/sats over UART      |
| `lora.ino`      | TTGO ESP32 ×2  | TX/RX loopback (toggle `MODE_TX/RX`) |
| `sd_card.ino`   | TTGO ESP32     | SD on HSPI: write + readback         |
| `servo.ino`     | Arduino Nano   | Servo sweep w/ deploy angles         |
| `hc-020k.ino`   | TTGO ESP32     | Photo-encoder pulse counter / RPM    |

---

## Wiring notes

- **I2C bus** (SDA=21, SCL=22) is shared by BNO055, ADXL375, MS5611, and INA219. All four addresses are unique. Use 4.7 kΩ pull-ups if the breakout boards do not include them.
- **SD card module** must be wired to the **HSPI** pins above, **not** the LoRa SPI pins. Sharing the LoRa bus risks corrupting in-flight transmissions.
- **GPS module** (`GY-NEO-6M`) operates at 3.3 V — do not power from the Nano's 5 V rail.
- **LoRa antenna** must be connected before powering up; transmitting without an antenna damages the SX1276 PA.
- **Battery**: 3.7 V 1000 mAh LiPo through INA219 → TTGO `VBAT` / 5 V regulator input.
- **Deployment board** is independently powered (separate battery or rocket BEC) for safety isolation from the flight computer.

---

## License

MIT — see `LICENSE`.
