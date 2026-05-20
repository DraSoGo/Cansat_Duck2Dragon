#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_ADXL375.h>

#define I2C_SDA 21
#define I2C_SCL 22

Adafruit_ADXL375 accel = Adafruit_ADXL375(12345);

void setup()
{
  Serial.begin(115200);
  while (!Serial);
  Wire.begin(I2C_SDA, I2C_SCL);

  if (!accel.begin())
  {
    Serial.println("ADXL375 not found");
    while (1) delay(1000);
  }
  accel.setDataRate(ADXL343_DATARATE_400_HZ);
  Serial.println("ADXL375 OK");
}

void loop()
{
  sensors_event_t event;
  accel.getEvent(&event);

  float gx = event.acceleration.x / 9.80665;
  float gy = event.acceleration.y / 9.80665;
  float gz = event.acceleration.z / 9.80665;

  Serial.print("X=");  Serial.print(gx, 2); Serial.print(" g\t");
  Serial.print("Y=");  Serial.print(gy, 2); Serial.print(" g\t");
  Serial.print("Z=");  Serial.print(gz, 2); Serial.println(" g");
  delay(200);
}
