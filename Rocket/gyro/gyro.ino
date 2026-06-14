/*
 * Duck2Dragon Data Logger v10
 * ============================
 * Hardware:
 *   - ESP32 NodeMCU WROOM-32UE
 *   - GY-63 MS5611 Barometer    (I2C 0x77, SDA=G21, SCL=G22)
 *   - ADXL345 Accelerometer    (I2C 0x53, SDA=G21, SCL=G22)
 *   - GPS Neo-6M / NEO-M8      (UART2, RX2=G16, TX2=G17)
 *
 * Behavior:
 *   - Power on  → starts logging immediately
 *   - Power off → data stays in NVS flash (Preferences)
 *   - Reconnect to PC → open Serial Monitor @ 115200 baud
 *     send 'd' to dump all records
 *     send 'e' to erase all records
 *     send 'i' to print info/count
 *
 * Storage: NVS namespace "log", keys "cnt" + "rXXXXX"
 * Max records: ~500 (NVS ~20 KB usable for this namespace)
 */

#include <Wire.h>
#include <Preferences.h>
#include <HardwareSerial.h>

// ── Pin Definitions ───────────────────────────────────────────────────
#define I2C_SDA       21
#define I2C_SCL       22
#define GPS_RX        16   // ESP32 receives from GPS TX
#define GPS_TX        17   // ESP32 transmits to GPS RX

// ── I2C Addresses ─────────────────────────────────────────────────────
#define MS5611_ADDR   0x77
#define ADXL345_ADDR  0x53

// ── Timing ────────────────────────────────────────────────────────────
#define LOG_INTERVAL_MS   1000   // log every 1 second
#define MAX_RECORDS       500    // limit NVS usage

// ── GPS Serial ────────────────────────────────────────────────────────
HardwareSerial GPS(2);  // UART2

// ── Preferences ───────────────────────────────────────────────────────
Preferences prefs;

// ── Data Record ───────────────────────────────────────────────────────
struct Record {
  uint32_t timestamp_ms;
  float    temperature_C;
  float    pressure_Pa;
  float    altitude_m;
  float    accel_x;   // g
  float    accel_y;
  float    accel_z;
  float    lat;
  float    lon;
  float    gps_alt;     // m (GPS altitude MSL)
  float    speed_knots;
  uint8_t  gps_fix;   // 0=no fix, 1=fix
  uint8_t  gps_sats;
};

// ── MS5611 Calibration Coefficients ───────────────────────────────────
uint16_t C[7];  // C[1]..C[6] from PROM

// ── Globals ───────────────────────────────────────────────────────────
uint32_t recordCount = 0;
unsigned long lastLogTime = 0;

// ── GPS Parsing State ─────────────────────────────────────────────────
String   gpsLine = "";
float    gps_lat = 0, gps_lon = 0, gps_speed = 0, gps_alt = 0;
uint8_t  gps_fix = 0, gps_sats = 0;
bool     gps_valid = false;

// ═══════════════════════════════════════════════════════════════════════
//  MS5611 Driver (minimal, no library dependency)
// ═══════════════════════════════════════════════════════════════════════
void ms5611_reset() {
  Wire.beginTransmission(MS5611_ADDR);
  Wire.write(0x1E);
  Wire.endTransmission();
  delay(5);
}

void ms5611_readPROM() {
  for (int i = 1; i <= 6; i++) {
    Wire.beginTransmission(MS5611_ADDR);
    Wire.write(0xA0 + (i * 2));
    Wire.endTransmission();
    Wire.requestFrom(MS5611_ADDR, 2);
    C[i] = ((uint16_t)Wire.read() << 8) | Wire.read();
  }
}

uint32_t ms5611_readRaw(uint8_t cmd) {
  Wire.beginTransmission(MS5611_ADDR);
  Wire.write(cmd);  // 0x48=D1 pressure, 0x58=D2 temperature (OSR=4096)
  Wire.endTransmission();
  delay(10);
  Wire.beginTransmission(MS5611_ADDR);
  Wire.write(0x00);
  Wire.endTransmission();
  Wire.requestFrom(MS5611_ADDR, 3);
  uint32_t val = ((uint32_t)Wire.read() << 16) |
                 ((uint32_t)Wire.read() << 8)  |
                 Wire.read();
  return val;
}

bool ms5611_read(float &temperature, float &pressure) {
  uint32_t D2 = ms5611_readRaw(0x58);
  uint32_t D1 = ms5611_readRaw(0x48);

  // Compensation (from MS5611 datasheet)
  int32_t dT   = (int32_t)D2 - ((int32_t)C[5] << 8);
  int32_t TEMP = 2000 + ((int64_t)dT * C[6]) / 8388608L;

  int64_t OFF  = ((int64_t)C[2] << 16) + ((int64_t)C[4] * dT) / 128;
  int64_t SENS = ((int64_t)C[1] << 15) + ((int64_t)C[3] * dT) / 256;

  // Second-order compensation
  int64_t T2 = 0, OFF2 = 0, SENS2 = 0;
  if (TEMP < 2000) {
    T2    = ((int64_t)dT * dT) / 2147483648LL;
    OFF2  = 5 * ((int64_t)(TEMP - 2000) * (TEMP - 2000)) / 2;
    SENS2 = 5 * ((int64_t)(TEMP - 2000) * (TEMP - 2000)) / 4;
    if (TEMP < -1500) {
      OFF2  += 7 * (int64_t)(TEMP + 1500) * (TEMP + 1500);
      SENS2 += 11 * (int64_t)(TEMP + 1500) * (TEMP + 1500) / 2;
    }
  }
  TEMP -= T2;
  OFF  -= OFF2;
  SENS -= SENS2;

  int32_t P = ((int64_t)D1 * SENS / 2097152L - OFF) / 32768L;

  temperature = TEMP / 100.0f;
  pressure    = P;       // Pa (divide by 100 for hPa/mbar)
  return (P > 10000);    // basic sanity check
}

float pressureToAltitude(float pressure_Pa, float seaLevel_Pa = 101325.0f) {
  return 44330.0f * (1.0f - powf(pressure_Pa / seaLevel_Pa, 0.1902949f));
}

// ═══════════════════════════════════════════════════════════════════════
//  ADXL345 Driver (minimal)
// ═══════════════════════════════════════════════════════════════════════
void adxl345_write(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(ADXL345_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

bool adxl345_init() {
  Wire.beginTransmission(ADXL345_ADDR);
  Wire.write(0x00);  // DEVID register
  Wire.endTransmission();
  Wire.requestFrom(ADXL345_ADDR, 1);
  if (Wire.read() != 0xE5) return false;
  adxl345_write(0x2D, 0x08);  // POWER_CTL: measure mode
  adxl345_write(0x31, 0x0B);  // DATA_FORMAT: full res, ±16g
  return true;
}

bool adxl345_read(float &ax, float &ay, float &az) {
  Wire.beginTransmission(ADXL345_ADDR);
  Wire.write(0x32);  // DATAX0
  Wire.endTransmission(false);
  Wire.requestFrom(ADXL345_ADDR, 6);
  if (Wire.available() < 6) return false;
  int16_t rx = (int16_t)(Wire.read() | (Wire.read() << 8));
  int16_t ry = (int16_t)(Wire.read() | (Wire.read() << 8));
  int16_t rz = (int16_t)(Wire.read() | (Wire.read() << 8));
  // full-res: 3.9 mg/LSB
  ax = rx * 0.0039f;
  ay = ry * 0.0039f;
  az = rz * 0.0039f;
  return true;
}

// ═══════════════════════════════════════════════════════════════════════
//  GPS NMEA Parser (GPRMC + GPGGA)
// ═══════════════════════════════════════════════════════════════════════
float nmeaLatLonToDecimal(const String &val, const String &dir) {
  if (val.length() < 4) return 0.0f;
  int dotPos = val.indexOf('.');
  int degDigits = dotPos - 2;
  float degrees = val.substring(0, degDigits).toFloat();
  float minutes = val.substring(degDigits).toFloat();
  float result  = degrees + minutes / 60.0f;
  if (dir == "S" || dir == "W") result = -result;
  return result;
}

String csvField(const String &s, int n) {
  int start = 0, count = 0;
  for (int i = 0; i <= (int)s.length(); i++) {
    if (i == (int)s.length() || s[i] == ',') {
      if (count == n) return s.substring(start, i);
      count++;
      start = i + 1;
    }
  }
  return "";
}

void parseNMEA(const String &line) {
  if (line.startsWith("$GPRMC") || line.startsWith("$GNRMC")) {
    // $GPRMC,time,status,lat,N/S,lon,E/W,speed,course,date,...
    String status = csvField(line, 2);
    if (status == "A") {
      gps_fix   = 1;
      gps_lat   = nmeaLatLonToDecimal(csvField(line, 3), csvField(line, 4));
      gps_lon   = nmeaLatLonToDecimal(csvField(line, 5), csvField(line, 6));
      gps_speed = csvField(line, 7).toFloat();
      gps_valid = true;
    } else {
      gps_fix   = 0;
      gps_valid = false;
    }
  } else if (line.startsWith("$GPGGA") || line.startsWith("$GNGGA")) {
    // $GPGGA,time,lat,N/S,lon,E/W,fix,sats,hdop,alt,M,...
    int fixQ = csvField(line, 6).toInt();
    gps_sats = csvField(line, 7).toInt();
    gps_alt  = csvField(line, 9).toFloat();   // altitude MSL (meters)
    if (fixQ == 0) gps_fix = 0;
  }
}

void readGPS() {
  while (GPS.available()) {
    char c = GPS.read();
    if (c == '\n') {
      gpsLine.trim();
      if (gpsLine.length() > 0) parseNMEA(gpsLine);
      gpsLine = "";
    } else if (c != '\r') {
      gpsLine += c;
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════
//  NVS Storage
// ═══════════════════════════════════════════════════════════════════════
void saveRecord(const Record &r) {
  if (recordCount >= MAX_RECORDS) return;

  char key[12];
  snprintf(key, sizeof(key), "r%05lu", (unsigned long)recordCount);

  prefs.putBytes(key, (const void*)&r, sizeof(Record));
  recordCount++;
  prefs.putUInt("cnt", recordCount);
}

bool loadRecord(uint32_t idx, Record &r) {
  char key[12];
  snprintf(key, sizeof(key), "r%05lu", (unsigned long)idx);
  size_t got = prefs.getBytes(key, (void*)&r, sizeof(Record));
  return (got == sizeof(Record));
}

// ═══════════════════════════════════════════════════════════════════════
//  Serial Commands (PC interface)
// ═══════════════════════════════════════════════════════════════════════
void dumpAllRecords() {
  Serial.printf("\n=== DATA DUMP: %lu records ===\n", (unsigned long)recordCount);
  Serial.println("idx,time_ms,temp_C,pres_Pa,alt_m,ax_g,ay_g,az_g,lat,lon,gps_alt_m,spd_kn,fix,sats");
  for (uint32_t i = 0; i < recordCount; i++) {
    Record r;
    if (loadRecord(i, r)) {
      Serial.printf("%lu,%lu,%.2f,%.2f,%.2f,%.4f,%.4f,%.4f,%.6f,%.6f,%.2f,%.2f,%d,%d\n",
        (unsigned long)i,
        (unsigned long)r.timestamp_ms,
        r.temperature_C, r.pressure_Pa, r.altitude_m,
        r.accel_x, r.accel_y, r.accel_z,
        r.lat, r.lon, r.gps_alt, r.speed_knots,
        r.gps_fix, r.gps_sats);
    }
  }
  Serial.println("=== END ===\n");
}

void eraseAllRecords() {
  Serial.println("Erasing all records...");
  prefs.clear();
  recordCount = 0;
  prefs.putUInt("cnt", 0);
  Serial.println("Done. 0 records remain.");
}

void printInfo() {
  Serial.printf("\n[INFO] Records stored : %lu / %d\n", (unsigned long)recordCount, MAX_RECORDS);
  Serial.printf("[INFO] Uptime         : %lu ms\n", millis());
  Serial.printf("[INFO] GPS fix        : %s (%d sats)\n", gps_fix ? "YES" : "NO", gps_sats);
  Serial.printf("[INFO] Logging        : %s\n", (recordCount < MAX_RECORDS) ? "ACTIVE" : "FULL - STOPPED");
  Serial.println("[INFO] Commands: 'd'=dump  'e'=erase  'i'=info\n");
}

void handleSerial() {
  if (!Serial.available()) return;
  char cmd = Serial.read();
  switch (cmd) {
    case 'd': case 'D': dumpAllRecords(); break;
    case 'e': case 'E': eraseAllRecords(); break;
    case 'i': case 'I': printInfo();       break;
    default: break;
  }
}

// ═══════════════════════════════════════════════════════════════════════
//  Setup
// ═══════════════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n\n=== Duck2Dragon Logger v10 ===");
  Serial.println("Commands: 'd'=dump  'e'=erase  'i'=info");

  // I2C
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000);

  // GPS UART
  GPS.begin(9600, SERIAL_8N1, GPS_RX, GPS_TX);
  Serial.println("[GPS] UART2 started @ 9600");

  // MS5611
  ms5611_reset();
  ms5611_readPROM();
  Serial.printf("[MS5611] PROM: %u %u %u %u %u %u\n",
                C[1], C[2], C[3], C[4], C[5], C[6]);

  // ADXL345
  if (adxl345_init()) {
    Serial.println("[ADXL345] OK");
  } else {
    Serial.println("[ADXL345] NOT FOUND! Check wiring.");
  }

  // Load record count from NVS
  prefs.begin("log", false);  // false = read/write
  recordCount = prefs.getUInt("cnt", 0);
  Serial.printf("[NVS] Existing records: %lu\n", (unsigned long)recordCount);

  if (recordCount >= MAX_RECORDS) {
    Serial.println("[NVS] Storage FULL. Send 'e' to erase before new flight.");
  }

  Serial.println("[LOGGER] Starting in 2s...\n");
  delay(2000);
}

// ═══════════════════════════════════════════════════════════════════════
//  Loop
// ═══════════════════════════════════════════════════════════════════════
void loop() {
  // Always drain GPS buffer
  readGPS();

  // Handle Serial commands from PC
  handleSerial();

  // Log at interval
  if (millis() - lastLogTime >= LOG_INTERVAL_MS) {
    lastLogTime = millis();

    if (recordCount >= MAX_RECORDS) {
      // Storage full, just keep reading GPS silently
      return;
    }

    Record r;
    r.timestamp_ms = millis();

    // MS5611
    float temp, pres;
    if (ms5611_read(temp, pres)) {
      r.temperature_C = temp;
      r.pressure_Pa   = pres;
      r.altitude_m    = pressureToAltitude(pres);
    } else {
      r.temperature_C = -999;
      r.pressure_Pa   = 0;
      r.altitude_m    = 0;
    }

    // ADXL345
    float ax, ay, az;
    if (adxl345_read(ax, ay, az)) {
      r.accel_x = ax;
      r.accel_y = ay;
      r.accel_z = az;
    } else {
      r.accel_x = r.accel_y = r.accel_z = 0;
    }

    // GPS
    r.lat        = gps_lat;
    r.lon        = gps_lon;
    r.gps_alt    = gps_alt;
    r.speed_knots = gps_speed;
    r.gps_fix    = gps_fix;
    r.gps_sats   = gps_sats;

    // Save
    saveRecord(r);

    // Serial status (always visible)
    Serial.printf("[%lu] #%lu | T=%.1f°C P=%.0fPa Alt=%.1fm | "
                  "A=%.2f,%.2f,%.2fg | GPS:%s(%.4f,%.4f) galt=%.1fm spd=%.1f\n",
      r.timestamp_ms, (unsigned long)(recordCount - 1),
      r.temperature_C, r.pressure_Pa, r.altitude_m,
      r.accel_x, r.accel_y, r.accel_z,
      r.gps_fix ? "FIX" : "---",
      r.lat, r.lon, r.gps_alt, r.speed_knots);
  }
  delay(10000);
}