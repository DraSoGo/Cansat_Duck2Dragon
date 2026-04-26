#include <Wire.h>
#include <Adafruit_INA219.h>

#define I2C_SDA 21
#define I2C_SCL 22

Adafruit_INA219 ina219;

void setup()
{
  Serial.begin(115200);
  while (!Serial);
  Wire.begin(I2C_SDA, I2C_SCL);

  if (!ina219.begin())
  {
    Serial.println("INA219 not found at 0x40");
    while (1) delay(1000);
  }
  Serial.println("INA219 OK");
}

void loop()
{
  float shunt   = ina219.getShuntVoltage_mV();
  float bus     = ina219.getBusVoltage_V();
  float current = ina219.getCurrent_mA();
  float power   = ina219.getPower_mW();
  float load    = bus + (shunt / 1000.0);

  Serial.print("Bus=");   Serial.print(bus, 3);     Serial.print(" V\t");
  Serial.print("Shunt="); Serial.print(shunt, 3);   Serial.print(" mV\t");
  Serial.print("Load=");  Serial.print(load, 3);    Serial.print(" V\t");
  Serial.print("I=");     Serial.print(current, 3); Serial.print(" mA\t");
  Serial.print("P=");     Serial.print(power, 3);   Serial.println(" mW");
  delay(500);
}
