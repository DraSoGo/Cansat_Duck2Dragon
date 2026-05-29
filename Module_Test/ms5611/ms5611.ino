// MS5611 barometer test — target board: Arduino Nano (Deployment module)
// I2C: A4=SDA, A5=SCL (hardware default on Nano)

#include <Wire.h>
#include <MS5611.h>

MS5611 ms5611(0x77);

void setup()
{
  Serial.begin(115200);
  while (!Serial);
  Wire.begin();
  delay(100);  // let VCC stabilize before PROM read

  if (!ms5611.begin())
  {
    Serial.println(F("MS5611 not found at 0x77"));
    while (1) delay(1000);
  }
  ms5611.setOversampling(OSR_HIGH);
  Serial.println(F("MS5611 OK"));
}

void loop()
{
  int r = ms5611.read();
  if (r != MS5611_READ_OK)
  {
    Serial.print(F("MS5611 read error: "));
    Serial.println(r);
    delay(500);
    return;
  }

  float pressure    = ms5611.getPressure();
  float temperature = ms5611.getTemperature();
  float altitude    = 44330.0 * (1.0 - pow(pressure / 1013.25, 0.1903));

  Serial.print(F("T="));   Serial.print(temperature, 2); Serial.print(F(" C\t"));
  Serial.print(F("P="));   Serial.print(pressure, 2);    Serial.print(F(" hPa\t"));
  Serial.print(F("Alt=")); Serial.print(altitude, 2);    Serial.println(F(" m"));
  delay(500);
}
