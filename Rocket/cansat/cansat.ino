// CANSAT Duck2Dragon — Main Flight Computer
// Target: TTGO SX1276 LoRa32 (ESP32)
//
// Sensors:  GPS NEO-6M (UART), BNO085 I2C 0x4A, ADXL375 I2C 0x54,
//           MS5611 I2C 0x77, INA219 I2C 0x40
// Storage:  LittleFS internal flash (~1.5 MB)
//           Auto-rotate: when full, delete oldest boot block, keep newest
// Telem:    LoRa SX1276 @ 922.525 MHz (Thailand 920-925 ISM band)
// NOTE:     Arduino IDE -> Tools -> Partition Scheme -> "Default 4MB with spiffs"
//
// CSV (24 fields):
// millis,lat,lon,alt_gps,sats,alt_baro,temp,pressure,
// ax,ay,az,gx,gy,gz,qw,qx,qy,qz,
// high_ax,high_ay,high_az,voltage,current,watt

#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <LittleFS.h>
#include <TinyGPS++.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BNO08x.h>
// #include <Adafruit_ADXL375.h>
#include <Adafruit_ADXL375.h>
#include <Adafruit_INA219.h>
#include <MS5611.h>

// ---------------- Pin map ----------------
// LoRa (VSPI, built-in)
#define LORA_SCK   5
#define LORA_MISO  19
#define LORA_MOSI  27
#define LORA_SS    18
#define LORA_RST   14
#define LORA_DIO0  26

// I2C bus
#define I2C_SDA    21
#define I2C_SCL    22

// GPS UART (HardwareSerial 1)
#define GPS_RX     3
#define GPS_TX     1
#define GPS_BAUD   9600

// ---------------- LoRa config ----------------
#define BAND       922250000
#define LORA_BW    125E3
#define LORA_SF    11

// ---------------- Storage config ----------------
#define LOG_PATH        "/cansat.csv"
#define FS_FULL_THRESH  0.90f   // rotate when 90% full
#define TRIM_BYTES      8192    // drop ~8 KB of oldest data per rotation

// ---------------- Loop timing ----------------
#define LOOP_PERIOD_MS  500   // ~2 Hz

// ---------------- Globals ----------------
HardwareSerial GPSserial(1);
TinyGPSPlus gps;

Adafruit_BNO08x   bno;
Adafruit_ADXL375  hgAccel = Adafruit_ADXL375(54321);
Adafruit_INA219   ina219(0x40);
MS5611            ms5611(0x77);

sh2_SensorValue_t bnoSensorValue;

bool okBNO = false, okMS = false, okINA = false, okADXL = false;

File    logFile;
bool    okFS = false;
const char* LOG_PATH_TMP = "/cansat_tmp.csv";

// ---------------- FS helpers ----------------

// Returns used/total ratio of LittleFS
float fsUsedRatio()
{
  return (float)LittleFS.usedBytes() / (float)LittleFS.totalBytes();
}

// Trim oldest TRIM_BYTES from log file by rewriting without first N bytes.
// Finds first newline after TRIM_BYTES offset so we don't cut mid-line.
void trimOldestData()
{
  logFile.close();

  File src = LittleFS.open(LOG_PATH, "r");
  if (!src) { okFS = false; return; }

  size_t fileSize = src.size();
  if (fileSize <= (size_t)TRIM_BYTES) {
    // File smaller than trim size — just wipe and restart
    src.close();
    LittleFS.remove(LOG_PATH);
    logFile = LittleFS.open(LOG_PATH, "a");
    okFS = logFile ? true : false;
    return;
  }

  // Seek past TRIM_BYTES, then find next newline
  src.seek(TRIM_BYTES);
  while (src.available() && src.peek() != '\n') src.read();
  if (src.available()) src.read(); // consume the '\n'

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

  // Replace original with trimmed
  LittleFS.remove(LOG_PATH);
  LittleFS.rename(LOG_PATH_TMP, LOG_PATH);

  // Reopen for append
  logFile = LittleFS.open(LOG_PATH, "a");
  okFS = logFile ? true : false;
}

// Check if FS near full; trim if needed
void checkAndTrim()
{
  if (fsUsedRatio() >= FS_FULL_THRESH)
  {
    trimOldestData();
  }
}

// ---------------- Setup ----------------
void setup()
{
  // Serial.begin(115200);
  delay(200);

  // GPS
  GPSserial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);

  // LoRa on VSPI
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_SS);
  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
  if (!LoRa.begin(BAND))
  {
    // Serial.println("# LoRa FAIL");
  }
  else
  {
    LoRa.setSignalBandwidth(LORA_BW);
    LoRa.setSpreadingFactor(LORA_SF);
    // Serial.println("# LoRa OK");
  }

  // I2C
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setTimeOut(100);
  for(int i = 0; i < 9 && !okBNO; i++)
  {
    okBNO = bno.begin_I2C(0x4A, &Wire);
    if (okBNO)
    {
      bno.enableReport(SH2_ROTATION_VECTOR);
      bno.enableReport(SH2_LINEAR_ACCELERATION);
      bno.enableReport(SH2_GYROSCOPE_CALIBRATED);
    }
  }
  for(int i = 0; i < 9 && !okMS; i++)
  {
    okMS = ms5611.begin();
    if (okMS) ms5611.setOversampling(OSR_HIGH);
    delay(50);
  }
  for(int i = 0; i < 9 && !okINA; i++)
  {
    okINA = ina219.begin();
  }
  for(int i = 0; i < 9 && !okADXL; i++)
  {
    okADXL = hgAccel.begin(0x54);
    if (okADXL) hgAccel.setDataRate(ADXL343_DATARATE_400_HZ);
  }

  // LittleFS
  if (LittleFS.begin(true))
  {
    okFS = true;
    logFile = LittleFS.open(LOG_PATH, "a");
    if (logFile)
    {
      logFile.println();  // blank line separator
      logFile.print("# boot millis=");
      logFile.println(millis());

      // Filesystem status
      String fsInfo = "# LittleFS: ";
      fsInfo += LittleFS.usedBytes();
      fsInfo += " / ";
      fsInfo += LittleFS.totalBytes();
      fsInfo += " bytes used (";
      fsInfo += (int)(fsUsedRatio() * 100);
      fsInfo += "%)";

      logFile.println(fsInfo);
      logFile.println("# millis,lat,lon,alt_gps,sats,alt_baro,temp,pressure,ax,ay,az,gx,gy,gz,qw,qx,qy,qz,high_ax,high_ay,high_az,voltage,current,watt");
      logFile.flush();

      // Transmit boot status via LoRa
      LoRa.beginPacket();
      LoRa.print("# boot millis=");
      LoRa.println(millis());
      LoRa.println(fsInfo);
      LoRa.endPacket();
      delay(100);
    }
    else { okFS = false; }
  }
}

// ---------------- Helpers ----------------
void feedGPS()
{
  while (GPSserial.available()) gps.encode(GPSserial.read());
}

void appendFloat(String& s, float v, uint8_t decimals)
{
  if (isnan(v)) v = 0.0;
  s += String(v, (unsigned int)decimals);
  s += ',';
}

void appendInt(String& s, long v)
{
  s += v;
  s += ',';
}

String buildCsvLine()
{
  String line;
  line.reserve(220);

  // 0: millis
  appendInt(line, millis());

  // 1-4: GPS lat, lon, alt, sats
  if (gps.location.isValid())
  {
    appendFloat(line, gps.location.lat(), 6);
    appendFloat(line, gps.location.lng(), 6);
  }
  else
  {
    line += "0.000000,0.000000,";
  }
  appendFloat(line, gps.altitude.isValid()   ? gps.altitude.meters() : 0.0, 2);
  appendInt  (line, gps.satellites.isValid() ? gps.satellites.value() : 0);

  // 5-7: MS5611 alt, temp, pressure
  if (okMS && ms5611.read() == MS5611_READ_OK)
  {
    float p = ms5611.getPressure() * 2.0;  // clone chip fix: factor-of-2 compensation
    float t = ms5611.getTemperature();
    float a = 44330.0 * (1.0 - pow(p / 1013.25, 0.1903));
    appendFloat(line, a, 2);
    appendFloat(line, t, 2);
    appendFloat(line, p, 2);
  }
  else
  {
    line += "0.00,0.00,0.00,";
  }

  // 8-17: BNO085 accel(3), gyro(3), quaternion(4)
  float bax = 0, bay = 0, baz = 0;
  float bgx = 0, bgy = 0, bgz = 0;
  float qw = 1, qx = 0, qy = 0, qz = 0;
  if (okBNO)
  {
    if (bno.getSensorEvent(&bnoSensorValue))
    {
      switch (bnoSensorValue.sensorId)
      {
        case SH2_LINEAR_ACCELERATION:
          bax = bnoSensorValue.un.linearAcceleration.x;
          bay = bnoSensorValue.un.linearAcceleration.y;
          baz = bnoSensorValue.un.linearAcceleration.z;
          break;
        case SH2_GYROSCOPE_CALIBRATED:
          bgx = bnoSensorValue.un.gyroscope.x;
          bgy = bnoSensorValue.un.gyroscope.y;
          bgz = bnoSensorValue.un.gyroscope.z;
          break;
        case SH2_ROTATION_VECTOR:
          qw = bnoSensorValue.un.rotationVector.real;
          qx = bnoSensorValue.un.rotationVector.i;
          qy = bnoSensorValue.un.rotationVector.j;
          qz = bnoSensorValue.un.rotationVector.k;
          break;
      }
    }
  }
  appendFloat(line, bax, 4);
  appendFloat(line, bay, 4);
  appendFloat(line, baz, 4);
  appendFloat(line, bgx, 4);
  appendFloat(line, bgy, 4);
  appendFloat(line, bgz, 4);
  appendFloat(line, qw, 4);
  appendFloat(line, qx, 4);
  appendFloat(line, qy, 4);
  appendFloat(line, qz, 4);

  // 18-20: ADXL375 high-G xyz
  float hax = 0, hay = 0, haz = 0;
  if (okADXL)
  {
    sensors_event_t hg;
    hgAccel.getEvent(&hg);
    hax = hg.acceleration.x / 9.80665f;
    hay = hg.acceleration.y / 9.80665f;
    haz = hg.acceleration.z / 9.80665f;
  }
  appendFloat(line, hax, 2);
  appendFloat(line, hay, 2);
  appendFloat(line, haz, 2);

  // 21-23: INA219 voltage, current, watt
  float bus = 0, cur = 0;
  if (okINA)
  {
    bus = ina219.getBusVoltage_V();
    cur = ina219.getCurrent_mA();
  }
  float watt = bus * (cur / 1000.0);

  line += String(bus, (unsigned int)3);
  line += ',';
  line += String(cur, (unsigned int)3);
  line += ',';
  line += String(watt, (unsigned int)3);
  line += ',';

  return line;
}

// ---------------- Loop ----------------
void loop()
{
  static uint32_t nextTick = 0;

  feedGPS();

  if (millis() < nextTick) return;
  nextTick = millis() + LOOP_PERIOD_MS;

  String csv = buildCsvLine();

  // Transmit via LoRa
  LoRa.beginPacket();
  LoRa.print(csv);
  LoRa.endPacket();

  // Log to LittleFS with auto-trim
  if (okFS && logFile)
  {
    checkAndTrim();   // rotate if ≥90% full
    if (okFS && logFile)
    {
      logFile.println(csv);
      logFile.flush();
    }
  }

  // Attempt re-init of failed sensors
  if (!okBNO)
  {
    okBNO = bno.begin_I2C(0x4A, &Wire);
    if (okBNO) { bno.enableReport(SH2_ROTATION_VECTOR); bno.enableReport(SH2_LINEAR_ACCELERATION); bno.enableReport(SH2_GYROSCOPE_CALIBRATED); }
  }
  if (!okMS)  { okMS  = ms5611.begin(); if (okMS) ms5611.setOversampling(OSR_HIGH); }
  if (!okINA)   okINA = ina219.begin();
  if (!okADXL) { okADXL = hgAccel.begin(0x54); if (okADXL) hgAccel.setDataRate(ADXL343_DATARATE_400_HZ); }
}
