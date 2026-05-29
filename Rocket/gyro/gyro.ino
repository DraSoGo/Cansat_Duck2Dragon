// CANSAT Duck2Dragon — Autogyro Data Logger (simplified)
// Target: ESP32 DevKit
//
// Sensors:  GPS NEO-M8 (UART2), MS5611 (I2C 0x77)
// Storage:  LittleFS internal flash (~1.5 MB)
//           Auto-trim: when 90% full, drop oldest 8 KB
//
// CSV (8 fields):
// millis,lat,lon,alt_gps,sats,alt_baro,temp,pressure

#include <Wire.h>
#include <LittleFS.h>
#include <TinyGPS++.h>
#include <MS5611.h>

// ---------------- Pin map ----------------
// I2C bus
#define I2C_SDA  21
#define I2C_SCL  22

// GPS UART (HardwareSerial 2)
#define GPS_RX   16
#define GPS_TX   17
#define GPS_BAUD 9600

// ---------------- Storage config ----------------
#define LOG_PATH        "/gyro.csv"
#define LOG_PATH_TMP    "/gyro_tmp.csv"
#define FS_FULL_THRESH  0.90f   // trim when 90% full
#define TRIM_BYTES      8192    // drop oldest 8 KB per trim

// ---------------- Loop timing ----------------
#define LOOP_PERIOD_MS 200  // 5 Hz

// ---------------- Globals ----------------
HardwareSerial GPSserial(2);
TinyGPSPlus    gps;
MS5611         ms5611(0x77);

File logFile;
bool okFS = false;
bool okMS = false;

// ---------------- FS trim ----------------
float fsUsedRatio()
{
  return (float)LittleFS.usedBytes() / (float)LittleFS.totalBytes();
}

void trimOldestData()
{
  logFile.close();

  File src = LittleFS.open(LOG_PATH, "r");
  if (!src) { okFS = false; return; }

  size_t fileSize = src.size();
  if (fileSize <= (size_t)TRIM_BYTES)
  {
    src.close();
    LittleFS.remove(LOG_PATH);
    logFile = LittleFS.open(LOG_PATH, "a");
    okFS = logFile ? true : false;
    return;
  }

  // Skip past TRIM_BYTES, align to next newline
  src.seek(TRIM_BYTES);
  while (src.available() && src.peek() != '\n') src.read();
  if (src.available()) src.read(); // consume '\n'

  // Write remainder to temp file
  File dst = LittleFS.open(LOG_PATH_TMP, "w");
  if (!dst) { src.close(); okFS = false; return; }

  uint8_t buf[256];
  while (src.available())
  {
    size_t n = src.read(buf, sizeof(buf));
    dst.write(buf, n);
  }
  src.close();
  dst.close();

  LittleFS.remove(LOG_PATH);
  LittleFS.rename(LOG_PATH_TMP, LOG_PATH);

  logFile = LittleFS.open(LOG_PATH, "a");
  okFS = logFile ? true : false;
}

void checkAndTrim()
{
  if (fsUsedRatio() >= FS_FULL_THRESH)
    trimOldestData();
}

// ---------------- Setup ----------------
void setup()
{
  Serial.begin(115200);
  delay(200);

  // GPS
  GPSserial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);

  // I2C + MS5611
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setTimeOut(100);
  for (int i = 0; i < 9 && !okMS; i++)
  {
    okMS = ms5611.begin();
    if (okMS) { ms5611.setOversampling(OSR_HIGH); Serial.println("# MS5611 OK"); }
    else Serial.println("# MS5611 FAIL");
    delay(50);
  }

  // LittleFS
  if (LittleFS.begin(true))
  {
    okFS = true;
    Serial.println("# LittleFS OK");
    logFile = LittleFS.open(LOG_PATH, "a");
    if (logFile)
    {
      logFile.print("# boot millis=");
      logFile.println(millis());
      logFile.println("# millis,lat,lon,alt_gps,sats,alt_baro,temp,pressure");
      logFile.flush();
      Serial.println("# Log: " LOG_PATH);
    }
    else { Serial.println("# File open FAIL"); okFS = false; }
  }
  else Serial.println("# LittleFS FAIL");
}

// ---------------- Helpers ----------------
void feedGPS()
{
  while (GPSserial.available()) gps.encode(GPSserial.read());
}

// ---------------- Loop ----------------
void loop()
{
  static uint32_t nextTick = 0;
  feedGPS();

  if (millis() < nextTick) return;
  nextTick = millis() + LOOP_PERIOD_MS;

  // MS5611
  float alt_baro = 0, temp = 0, pressure = 0;
  if (okMS && ms5611.read() == MS5611_READ_OK)
  {
    pressure = ms5611.getPressure();
    temp     = ms5611.getTemperature();
    alt_baro = 44330.0f * (1.0f - pow(pressure / 1013.25f, 0.1903f));
  }
  else if (!okMS)
  {
    okMS = ms5611.begin();
    if (okMS) ms5611.setOversampling(OSR_HIGH);
  }

  // GPS
  float lat     = gps.location.isValid()   ? gps.location.lat()     : 0.0f;
  float lon     = gps.location.isValid()   ? gps.location.lng()     : 0.0f;
  float alt_gps = gps.altitude.isValid()   ? gps.altitude.meters()  : 0.0f;
  int   sats    = gps.satellites.isValid() ? gps.satellites.value() : 0;

  // Build CSV
  char line[160];
  snprintf(line, sizeof(line),
    "%lu,%.6f,%.6f,%.2f,%d,%.2f,%.2f,%.2f",
    millis(), lat, lon, alt_gps, sats, alt_baro, temp, pressure);

  // Write to LittleFS with auto-trim
  if (okFS && logFile)
  {
    checkAndTrim();
    if (okFS && logFile)
    {
      logFile.println(line);
      logFile.flush();
    }
  }

  Serial.println(line);
}
