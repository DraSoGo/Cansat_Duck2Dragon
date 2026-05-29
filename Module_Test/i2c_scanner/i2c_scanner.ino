// I2C Scanner — works on both Arduino Nano and ESP32 (TTGO)
// Nano:  SDA=A4, SCL=A5 (hardware default, no args needed)
// ESP32: SDA=21, SCL=22 (set below)
//
// Scans 0x01–0x7E, prints found addresses + known device names.

#include <Wire.h>

// ---- Board select ----
// Comment out one:
// #define BOARD_NANO
#define BOARD_ESP32

#ifdef BOARD_ESP32
  #define I2C_SDA 21
  #define I2C_SCL 22
#endif

// Known I2C addresses
const char* knownDevice(uint8_t addr) {
  switch (addr) {
    case 0x28: return "BNO055 (ADR=LOW) / BNO08x";
    case 0x29: return "BNO055 (ADR=HIGH)";
    case 0x3C: return "OLED SSD1306 (128x64)";
    case 0x3D: return "OLED SSD1306 alt";
    case 0x40: return "INA219 (A0=GND,A1=GND)";
    case 0x41: return "INA219 alt";
    case 0x44: return "SHT30/SHT31";
    case 0x48: return "ADS1115 / TMP102";
    case 0x4A: return "BNO08x (PS1=0,PS0=1)";
    case 0x53: return "ADXL345/375 (SDO=LOW)";
    case 0x54: return "ADXL345/375 (SDO=HIGH)";
    case 0x57: return "MAX30102 (HR sensor)";
    case 0x68: return "DS3231 RTC / MPU6050";
    case 0x69: return "MPU6050 (AD0=HIGH)";
    case 0x76: return "BMP280/BME280 (SDO=LOW) / MS5611 (CSB=LOW)";
    case 0x77: return "BMP280/BME280 (SDO=HIGH) / MS5611 (CSB=HIGH)";
    default:   return nullptr;
  }
}

void setup() {
  Serial.begin(115200);
  while (!Serial);
  delay(500);

#ifdef BOARD_ESP32
  Wire.begin(I2C_SDA, I2C_SCL);
  Serial.println("I2C Scanner — ESP32 (SDA=21, SCL=22)");
#else
  Wire.begin();
  Serial.println("I2C Scanner — Arduino Nano (SDA=A4, SCL=A5)");
#endif

  Serial.println("Scanning 0x01–0x7E...\n");

  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    uint8_t err = Wire.endTransmission();
    if (err == 0) {
      Serial.print("  0x");
      if (addr < 16) Serial.print("0");
      Serial.print(addr, HEX);
      const char* name = knownDevice(addr);
      if (name) {
        Serial.print("  →  ");
        Serial.print(name);
      }
      Serial.println();
      found++;
    }
  }

  Serial.println();
  Serial.print("Done. Found ");
  Serial.print(found);  
  Serial.println(" device(s).");
}

void loop() {}
