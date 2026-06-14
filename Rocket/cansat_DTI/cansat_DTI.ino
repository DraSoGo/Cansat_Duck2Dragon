// CANSAT Duck2Dragon — DTI Telemetry Only
// Target: TTGO SX1276 LoRa32 (ESP32)
// Sends: TeamID, AccelMagnitude, Watt, Voltage, Ampere, Lat, Lon

#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <TinyGPS++.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BNO08x.h>
#include <Adafruit_INA219.h>

// ---------------- Config ----------------
#define TEAM_NUMBER     13    // Change to your team number

// Pin map
#define LORA_SCK   5
#define LORA_MISO  19
#define LORA_MOSI  27
#define LORA_SS    18
#define LORA_RST   14
#define LORA_DIO0  26

#define I2C_SDA    21
#define I2C_SCL    22

#define GPS_RX     3
#define GPS_TX     1
#define GPS_BAUD   9600

// LoRa config
#define BAND       922250000
#define LORA_BW    125E3
#define LORA_SF    11

#define LOOP_PERIOD_MS  1000   // 1 Hz

// ---------------- Globals ----------------
HardwareSerial GPSserial(1);
TinyGPSPlus gps;

Adafruit_BNO08x   bno;
Adafruit_INA219   ina219(0x40);

sh2_SensorValue_t bnoSensorValue;

bool okBNO = false, okINA = false;

// ---------------- Setup ----------------
void setup()
{
  delay(200);

  // GPS
  GPSserial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);

  // LoRa
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_SS);
  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
  if (LoRa.begin(BAND))
  {
    LoRa.setSignalBandwidth(LORA_BW);
    LoRa.setSpreadingFactor(LORA_SF);
  }

  // I2C
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setTimeOut(100);

  // BNO085
  for(int i = 0; i < 9 && !okBNO; i++)
  {
    okBNO = bno.begin_I2C(0x4A, &Wire);
    if (okBNO)
    {
      bno.enableReport(SH2_LINEAR_ACCELERATION);
    }
  }

  // INA219
  for(int i = 0; i < 9 && !okINA; i++)
  {
    okINA = ina219.begin();
  }
}

// ---------------- Helpers ----------------
void feedGPS()
{
  while (GPSserial.available()) gps.encode(GPSserial.read());
}

String buildDtiPacket()
{
  String pkt;
  pkt.reserve(80);

  // Team number
  pkt += TEAM_NUMBER;
  pkt += ',';

  // Accel magnitude from BNO085
  float ax = 0, ay = 0, az = 0;
  if (okBNO)
  {
    if (bno.getSensorEvent(&bnoSensorValue))
    {
      if (bnoSensorValue.sensorId == SH2_LINEAR_ACCELERATION)
      {
        ax = bnoSensorValue.un.linearAcceleration.x;
        ay = bnoSensorValue.un.linearAcceleration.y;
        az = bnoSensorValue.un.linearAcceleration.z;
      }
    }
  }
  float accel_mag = sqrt(ax*ax + ay*ay + az*az);
  pkt += String(accel_mag, 3);
  pkt += ',';

  // Power, Voltage, Current from INA219
  float voltage = 0, current_mA = 0;
  if (okINA)
  {
    voltage = ina219.getBusVoltage_V();
    current_mA = ina219.getCurrent_mA();
  }
  float watt = voltage * (current_mA / 1000.0);

  pkt += String(watt, 3);
  pkt += ',';
  pkt += String(voltage, 3);
  pkt += ',';
  pkt += String(current_mA / 1000.0, 3);  // Convert mA to A
  pkt += ',';

  // GPS lat, lon
  if (gps.location.isValid())
  {
    pkt += String(gps.location.lat(), 6);
    pkt += ',';
    pkt += String(gps.location.lng(), 6);
    pkt += ',';
    pkt += String(gps.satellites.value());
  }
  else
  {
    pkt += "0.000000,0.000000,0";
  }

  return pkt;
}

// ---------------- Loop ----------------
void loop()
{
  static uint32_t nextTick = 0;

  feedGPS();

  if (millis() < nextTick) return;
  nextTick = millis() + LOOP_PERIOD_MS;

  String packet = buildDtiPacket();

  // Transmit via LoRa
  LoRa.beginPacket();
  LoRa.print(packet);
  LoRa.endPacket();

  // Attempt re-init of failed sensors
  if (!okBNO)
  {
    okBNO = bno.begin_I2C(0x4A, &Wire);
    if (okBNO) bno.enableReport(SH2_LINEAR_ACCELERATION);
  }
  if (!okINA) okINA = ina219.begin();
}
