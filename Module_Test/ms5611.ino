#include <Wire.h>
#include <MS5611.h>

#define I2C_SDA 21
#define I2C_SCL 22

MS5611 ms5611(0x77);

void setup()
{
  Serial.begin(115200);
  while (!Serial);
  Wire.begin(I2C_SDA, I2C_SCL);

  if (!ms5611.begin())
  {
    Serial.println("MS5611 not found at 0x77");
    while (1) delay(1000);
  }
  ms5611.setOversampling(OSR_HIGH);
  Serial.println("MS5611 OK");
}

void loop()
{
  int r = ms5611.read();
  if (r != MS5611_READ_OK)
  {
    Serial.print("MS5611 read error: ");
    Serial.println(r);
    delay(500);
    return;
  }

  float pressure    = ms5611.getPressure();
  float temperature = ms5611.getTemperature();
  float altitude    = 44330.0 * (1.0 - pow(pressure / 1013.25, 0.1903));

  Serial.print("T=");   Serial.print(temperature, 2); Serial.print(" C\t");
  Serial.print("P=");   Serial.print(pressure, 2);    Serial.print(" hPa\t");
  Serial.print("Alt="); Serial.print(altitude, 2);    Serial.println(" m");
  delay(500);
}
