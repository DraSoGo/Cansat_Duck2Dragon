// MS5611 PROM diagnostic — Arduino Nano
// Reads raw PROM calibration words directly via I2C
// Prints each word + CRC check result
// Run multiple times — if values change between resets = power noise

#include <Wire.h>

#define MS5611_ADDR   0x77
#define CMD_RESET     0x1E
#define CMD_PROM_READ 0xA0  // + (i << 1) for each word 0-7

uint16_t prom[8];

uint8_t crc4(uint16_t* n_prom)
{
  uint16_t n_rem = 0;
  uint16_t crc_read = n_prom[7] & 0x000F;
  n_prom[7] &= 0xFF00;

  for (uint8_t cnt = 0; cnt < 16; cnt++)
  {
    if (cnt % 2 == 1) n_rem ^= (n_prom[cnt >> 1] & 0x00FF);
    else              n_rem ^= (n_prom[cnt >> 1] >> 8);
    for (uint8_t n_bit = 8; n_bit > 0; n_bit--)
    {
      if (n_rem & 0x8000) n_rem = (n_rem << 1) ^ 0x3000;
      else                n_rem <<= 1;
    }
  }
  n_rem = (n_rem >> 12) & 0x000F;
  n_prom[7] |= crc_read;
  return (n_rem ^ 0x00);
}

uint16_t readPROM(uint8_t index)
{
  Wire.beginTransmission(MS5611_ADDR);
  Wire.write(CMD_PROM_READ + (index << 1));
  Wire.endTransmission();
  Wire.requestFrom(MS5611_ADDR, 2);
  uint16_t val = (Wire.read() << 8) | Wire.read();
  return val;
}

void resetSensor()
{
  Wire.beginTransmission(MS5611_ADDR);
  Wire.write(CMD_RESET);
  Wire.endTransmission();
  delay(10);
}

void setup()
{
  Serial.begin(115200);
  while (!Serial);
  Wire.begin();
  delay(100);

  Serial.println(F("=== MS5611 PROM Diagnostic ==="));
  Serial.println(F("Reading PROM 5 times. Values should be IDENTICAL each time."));
  Serial.println(F("If values change = power noise / bad connection.\n"));

  for (uint8_t run = 1; run <= 5; run++)
  {
    resetSensor();
    delay(50);

    Serial.print(F("Run ")); Serial.print(run); Serial.println(F(":"));
    for (uint8_t i = 0; i < 8; i++)
    {
      prom[i] = readPROM(i);
      Serial.print(F("  PROM[")); Serial.print(i); Serial.print(F("] = 0x"));
      if (prom[i] < 0x1000) Serial.print(F("0"));
      Serial.print(prom[i], HEX);
      Serial.print(F("  ("));
      Serial.print(prom[i]);
      Serial.println(F(")"));
    }

    uint8_t crc_calc = crc4(prom);
    uint8_t crc_stored = prom[7] & 0x000F;
    Serial.print(F("  CRC stored=0x")); Serial.print(crc_stored, HEX);
    Serial.print(F("  CRC calc=0x"));   Serial.print(crc_calc, HEX);
    if (crc_calc == crc_stored)
      Serial.println(F("  ✓ MATCH"));
    else
      Serial.println(F("  ✗ MISMATCH ← noise/bad wiring"));

    Serial.println();
    delay(200);
  }

  Serial.println(F("=== Done. Check if PROM values consistent across runs ==="));
}

void loop() {}
