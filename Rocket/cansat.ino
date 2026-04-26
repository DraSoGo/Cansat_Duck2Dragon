// CANSAT Duck2Dragon — Main Flight Computer
// Target: TTGO SX1276 LoRa32 (ESP32)
//
// Sensors:  GPS NEO-6M (UART), BNO055 (I2C), ADXL375 (I2C),
//           MS5611 (I2C), INA219 (I2C)
// Storage:  microSD on HSPI
// Telem:    LoRa SX1276 @ 922.525 MHz (Thailand 920-925 ISM band)
//
// CSV (23 fields):
// millis,lat,lon,alt_gps,sats,alt_baro,temp,pressure,
// ax,ay,az,gx,gy,gz,qw,qx,qy,qz,
// high_ax,high_ay,high_az,voltage,current

#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <SD.h>
#include <TinyGPS++.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BNO055.h>
#include <Adafruit_ADXL375.h>
#include <Adafruit_INA219.h>
#include <MS5611.h>
#include <utility/imumaths.h>

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
#define GPS_RX     16
#define GPS_TX     17
#define GPS_BAUD   9600

// SD card (HSPI — separate from LoRa VSPI)
#define SD_CS      13
#define SD_MOSI    15
#define SD_MISO    2
#define SD_CLK     4

// ---------------- LoRa config ----------------
#define BAND       922525000
#define LORA_BW    125E3
#define LORA_SF    9

// ---------------- Loop timing ----------------
#define LOOP_PERIOD_MS  500   // ~2 Hz

// ---------------- Globals ----------------
HardwareSerial GPSserial(1);
TinyGPSPlus gps;

Adafruit_BNO055   bno     = Adafruit_BNO055(55, 0x28);
Adafruit_ADXL375  hgAccel = Adafruit_ADXL375(54321);
Adafruit_INA219   ina219;
MS5611            ms5611(0x77);

SPIClass hspi(HSPI);
File logFile;
const char* LOG_PATH = "/cansat.csv";

bool okBNO = false, okADXL = false, okMS = false, okINA = false, okSD = false;

// ---------------- Setup ----------------
void setup()
{
  Serial.begin(115200);
  delay(200);

  // GPS
  GPSserial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);

  // LoRa on VSPI
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_SS);
  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
  if (!LoRa.begin(BAND))
  {
    Serial.println("# LoRa FAIL");
  }
  else
  {
    LoRa.setSignalBandwidth(LORA_BW);
    LoRa.setSpreadingFactor(LORA_SF);
    Serial.println("# LoRa OK");
  }

  // I2C
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setTimeOut(100);

  okBNO  = bno.begin();      if (okBNO)  { bno.setExtCrystalUse(true); Serial.println("# BNO055 OK"); }   else Serial.println("# BNO055 FAIL");
  okADXL = hgAccel.begin();  if (okADXL) { hgAccel.setDataRate(ADXL343_DATARATE_400_HZ); Serial.println("# ADXL375 OK"); } else Serial.println("# ADXL375 FAIL");
  okMS   = ms5611.begin();   if (okMS)   { ms5611.setOversampling(OSR_HIGH); Serial.println("# MS5611 OK"); } else Serial.println("# MS5611 FAIL");
  okINA  = ina219.begin();   if (okINA)  Serial.println("# INA219 OK");  else Serial.println("# INA219 FAIL");

  // SD on HSPI
  hspi.begin(SD_CLK, SD_MISO, SD_MOSI, SD_CS);
  if (SD.begin(SD_CS, hspi))
  {
    okSD = true;
    Serial.println("# SD OK");

    logFile = SD.open(LOG_PATH, FILE_APPEND);
    if (logFile)
    {
      logFile.print("# boot millis=");
      logFile.println(millis());
      logFile.println("# millis,lat,lon,alt_gps,sats,alt_baro,temp,pressure,ax,ay,az,gx,gy,gz,qw,qx,qy,qz,high_ax,high_ay,high_az,voltage,current");
      logFile.flush();
    }
  }
  else
  {
    Serial.println("# SD FAIL");
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
  s += String(v, decimals);
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
    float p = ms5611.getPressure();
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

  // 8-17: BNO055 accel(3), gyro(3), quaternion(4)
  if (okBNO)
  {
    sensors_event_t accelEv, gyroEv;
    bno.getEvent(&accelEv, Adafruit_BNO055::VECTOR_LINEARACCEL);
    bno.getEvent(&gyroEv,  Adafruit_BNO055::VECTOR_GYROSCOPE);
    imu::Quaternion q = bno.getQuat();

    appendFloat(line, accelEv.acceleration.x, 4);
    appendFloat(line, accelEv.acceleration.y, 4);
    appendFloat(line, accelEv.acceleration.z, 4);
    appendFloat(line, gyroEv.gyro.x, 4);
    appendFloat(line, gyroEv.gyro.y, 4);
    appendFloat(line, gyroEv.gyro.z, 4);
    appendFloat(line, q.w(), 4);
    appendFloat(line, q.x(), 4);
    appendFloat(line, q.y(), 4);
    appendFloat(line, q.z(), 4);
  }
  else
  {
    line += "0.0000,0.0000,0.0000,0.0000,0.0000,0.0000,1.0000,0.0000,0.0000,0.0000,";
  }

  // 18-20: ADXL375 high-G xyz (in g units)
  if (okADXL)
  {
    sensors_event_t hg;
    hgAccel.getEvent(&hg);
    appendFloat(line, hg.acceleration.x / 9.80665, 2);
    appendFloat(line, hg.acceleration.y / 9.80665, 2);
    appendFloat(line, hg.acceleration.z / 9.80665, 2);
  }
  else
  {
    line += "0.00,0.00,0.00,";
  }

  // 21-22: INA219 voltage, current  (LAST two fields — no trailing comma)
  float bus = 0, cur = 0;
  if (okINA)
  {
    bus = ina219.getBusVoltage_V();
    cur = ina219.getCurrent_mA();
  }
  line += String(bus, 3);
  line += ',';
  line += String(cur, 3);

  return line;
}

// ---------------- Loop ----------------
void loop()
{
  static uint32_t nextTick = 0;

  // Always feed GPS parser
  feedGPS();

  if (millis() < nextTick) return;
  nextTick = millis() + LOOP_PERIOD_MS;

  String csv = buildCsvLine();

  // Transmit via LoRa
  LoRa.beginPacket();
  LoRa.print(csv);
  LoRa.endPacket();

  // Log to SD
  if (okSD && logFile)
  {
    logFile.println(csv);
    logFile.flush();
  }

  // Local debug
  Serial.println(csv);

  // Attempt re-init of failed sensors
  if (!okBNO)  okBNO  = bno.begin();
  if (!okADXL) okADXL = hgAccel.begin();
  if (!okMS)   okMS   = ms5611.begin();
  if (!okINA)  okINA  = ina219.begin();
}
