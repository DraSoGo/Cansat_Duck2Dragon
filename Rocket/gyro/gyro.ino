// CANSAT Duck2Dragon — Autogyro Data Logger
// Target: ESP32 (generic devkit or TTGO without LoRa used)
//
// Sensors:  GPS NEO-M8 (UART), ADXL375 (I2C), HW291 Honeywell HSC (I2C),
//           DS3231 RTC (I2C), HC-020K encoder (interrupt)
// Storage:  microSD on SPI (VSPI default: CS=G5, CLK=G18, MISO=G19, MOSI=G23)
//
// CSV (12 fields):
// datetime,lat,lon,alt_gps,sats,pressure_raw,pressure_psi,temp_hw291,
// high_ax,high_ay,high_az,rpm

#include <SPI.h>
#include <SD.h>
#include <Wire.h>
#include <RTClib.h>
#include <TinyGPS++.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_ADXL375.h>

// ---------------- Pin map ----------------
// SPI (VSPI default)
#define SD_CS      5
#define SD_CLK     18
#define SD_MISO    19
#define SD_MOSI    23

// I2C bus (shared)
#define I2C_SDA    21
#define I2C_SCL    22

// GPS UART (HardwareSerial 2)
#define GPS_RX     16
#define GPS_TX     17
#define GPS_BAUD   9600

// HC-020K encoder
#define HC020K_PIN 34
#define HC020K_PPR 20    // slots per revolution — adjust to disc

// ---------------- HW291 (Honeywell HSC/SSC) ----------------
// I2C address: 0x28 (default) or 0x38 (ALT)
// Output count = 14-bit pressure, 11-bit temperature
// P_min/P_max depend on part number (0–1 psi, 0–15 psi, etc.)
// Set these to match your specific HW291 part:
#define HW291_ADDR    0x28
#define HW291_P_MIN   0.0     // psi minimum of sensor range
#define HW291_P_MAX   1.0     // psi maximum of sensor range (check label)

// ---------------- Loop timing ----------------
#define LOOP_PERIOD_MS  200   // 5 Hz

// ---------------- HW291 data type ----------------
struct HW291Data { float psi; float tempC; bool ok; };

// ---------------- Globals ----------------
HardwareSerial GPSserial(2);
TinyGPSPlus    gps;

RTC_DS3231       rtc;
Adafruit_ADXL375 adxl = Adafruit_ADXL375(54321);

File    logFile;
bool    okRTC  = false;
bool    okADXL = false;
bool    okSD   = false;

volatile uint32_t pulseCount = 0;
uint32_t lastPulseCount = 0;
uint32_t lastRpmCalc    = 0;
float    currentRpm     = 0.0;

void IRAM_ATTR pulseISR()
{
  pulseCount = pulseCount + 1;
}

// ---------------- HW291 read ----------------
HW291Data readHW291()
{
  HW291Data d = {0, 0, false};
  Wire.requestFrom((uint8_t)HW291_ADDR, (uint8_t)4);
  if (Wire.available() < 4) return d;

  uint8_t b0 = Wire.read();
  uint8_t b1 = Wire.read();
  uint8_t b2 = Wire.read();
  uint8_t b3 = Wire.read();

  uint8_t status = (b0 >> 6) & 0x03;
  if (status == 0x02 || status == 0x03) return d; // stale or fault

  uint16_t rawP = ((uint16_t)(b0 & 0x3F) << 8) | b1;
  uint16_t rawT = ((uint16_t)b2 << 3) | (b3 >> 5);

  // Transfer function: psi = (rawP - 1638) * (P_MAX - P_MIN) / (14745 - 1638) + P_MIN
  d.psi  = ((float)rawP - 1638.0f) * (HW291_P_MAX - HW291_P_MIN) / 13107.0f + HW291_P_MIN;
  d.tempC = (float)rawT * 200.0f / 2047.0f - 50.0f;
  d.ok   = true;
  return d;
}

// ---------------- RPM calc ----------------
float calcRpm()
{
  uint32_t now = millis();
  uint32_t dt  = now - lastRpmCalc;
  if (dt < 100) return currentRpm;

  noInterrupts();
  uint32_t cur = pulseCount;
  interrupts();

  uint32_t delta = cur - lastPulseCount;
  lastPulseCount = cur;
  lastRpmCalc    = now;

  currentRpm = ((float)delta / HC020K_PPR) * (60000.0f / (float)dt);
  return currentRpm;
}

// ---------------- Setup ----------------
void setup()
{
  Serial.begin(115200);
  delay(200);

  // GPS
  GPSserial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);

  // I2C
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setTimeOut(100);

  // RTC
  okRTC = rtc.begin();
  if (okRTC)
  {
    if (rtc.lostPower()) rtc.adjust(DateTime(F(__DATE__), F(__TIME__)));
    Serial.println("# DS3231 OK");
  }
  else Serial.println("# DS3231 FAIL");

  // ADXL375
  okADXL = adxl.begin();
  if (okADXL) { adxl.setDataRate(ADXL343_DATARATE_400_HZ); Serial.println("# ADXL375 OK"); }
  else Serial.println("# ADXL375 FAIL");

  // HC-020K
  pinMode(HC020K_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(HC020K_PIN), pulseISR, RISING);
  lastRpmCalc = millis();

  // SD (VSPI default pins)
  if (SD.begin(SD_CS))
  {
    okSD = true;
    Serial.println("# SD OK");

    // Generate filename from RTC date: /GYRyddmmhh.csv
    char fname[20];
    if (okRTC)
    {
      DateTime now = rtc.now();
      snprintf(fname, sizeof(fname), "/GYRO%02u%02u%02u.csv",
               now.year() % 100, now.month(), now.day());
    }
    else
    {
      strcpy(fname, "/GYRO.csv");
    }

    logFile = SD.open(fname, FILE_APPEND);
    if (logFile)
    {
      logFile.println("# datetime,lat,lon,alt_gps,sats,pressure_raw_count,pressure_psi,temp_hw291_C,high_ax_g,high_ay_g,high_az_g,rpm");
      logFile.flush();
      Serial.print("# Log: "); Serial.println(fname);
    }
    else { Serial.println("# File open FAIL"); okSD = false; }
  }
  else Serial.println("# SD FAIL");
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

  // RTC datetime string
  char dtbuf[20] = "0000-00-00T00:00:00";
  if (okRTC)
  {
    DateTime now = rtc.now();
    snprintf(dtbuf, sizeof(dtbuf), "%04u-%02u-%02uT%02u:%02u:%02u",
             now.year(), now.month(), now.day(),
             now.hour(), now.minute(), now.second());
  }

  // GPS
  float lat     = gps.location.isValid() ? gps.location.lat()     : 0.0;
  float lon     = gps.location.isValid() ? gps.location.lng()     : 0.0;
  float alt_gps = gps.altitude.isValid() ? gps.altitude.meters()  : 0.0;
  int   sats    = gps.satellites.isValid()? gps.satellites.value() : 0;

  // HW291 pressure
  HW291Data hw = readHW291();
  uint16_t rawP = 0;
  float psi = 0, tempC = 0;
  if (hw.ok) { psi = hw.psi; tempC = hw.tempC; }
  // Re-read raw count for logging (recalc from psi)
  rawP = (uint16_t)((psi - HW291_P_MIN) / (HW291_P_MAX - HW291_P_MIN) * 13107.0f + 1638.5f);

  // ADXL375
  float ax = 0, ay = 0, az = 0;
  if (okADXL)
  {
    sensors_event_t ev;
    adxl.getEvent(&ev);
    ax = ev.acceleration.x / 9.80665f;
    ay = ev.acceleration.y / 9.80665f;
    az = ev.acceleration.z / 9.80665f;
  }

  // RPM
  float rpm = calcRpm();

  // Build CSV line
  char line[200];
  snprintf(line, sizeof(line),
    "%s,%.6f,%.6f,%.2f,%d,%u,%.4f,%.2f,%.2f,%.2f,%.2f,%.1f",
    dtbuf, lat, lon, alt_gps, sats,
    (unsigned)rawP, psi, tempC,
    ax, ay, az, rpm);

  // Write to SD
  if (okSD && logFile)
  {
    logFile.println(line);
    logFile.flush();
  }

  // Serial debug
  Serial.println(line);

  // Re-init failed sensors (throttled)
  static uint32_t lastRetry = 0;
  if (millis() - lastRetry > 5000)
  {
    lastRetry = millis();
    if (!okRTC)  okRTC  = rtc.begin();
    if (!okADXL) okADXL = adxl.begin();
  }
}
