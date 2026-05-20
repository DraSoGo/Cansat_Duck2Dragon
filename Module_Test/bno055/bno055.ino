#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BNO055.h>
#include <utility/imumaths.h>

#define I2C_SDA 21
#define I2C_SCL 22

Adafruit_BNO055 bno = Adafruit_BNO055(55, 0x28);

void setup()
{
  Serial.begin(115200);
  while (!Serial);
  Wire.begin(I2C_SDA, I2C_SCL);

  if (!bno.begin())
  {
    Serial.println("BNO055 not found at 0x28");
    while (1) delay(1000);
  }
  bno.setExtCrystalUse(true);
  Serial.println("BNO055 OK");
}

void loop()
{
  sensors_event_t orientationData, accelData, gyroData;
  bno.getEvent(&orientationData, Adafruit_BNO055::VECTOR_EULER);
  bno.getEvent(&accelData,       Adafruit_BNO055::VECTOR_LINEARACCEL);
  bno.getEvent(&gyroData,        Adafruit_BNO055::VECTOR_GYROSCOPE);
  imu::Quaternion q = bno.getQuat();

  uint8_t sys, gyro, accel, mag;
  bno.getCalibration(&sys, &gyro, &accel, &mag);

  Serial.print("Euler  H="); Serial.print(orientationData.orientation.x, 2);
  Serial.print(" R=");        Serial.print(orientationData.orientation.y, 2);
  Serial.print(" P=");        Serial.println(orientationData.orientation.z, 2);

  Serial.print("Accel  ");    Serial.print(accelData.acceleration.x, 4); Serial.print(", ");
  Serial.print(accelData.acceleration.y, 4); Serial.print(", ");
  Serial.println(accelData.acceleration.z, 4);

  Serial.print("Gyro   ");    Serial.print(gyroData.gyro.x, 4); Serial.print(", ");
  Serial.print(gyroData.gyro.y, 4); Serial.print(", ");
  Serial.println(gyroData.gyro.z, 4);

  Serial.print("Quat   w="); Serial.print(q.w(), 4); Serial.print(" x=");
  Serial.print(q.x(), 4); Serial.print(" y=");
  Serial.print(q.y(), 4); Serial.print(" z=");
  Serial.println(q.z(), 4);

  Serial.print("Calib  S="); Serial.print(sys);
  Serial.print(" G=");        Serial.print(gyro);
  Serial.print(" A=");        Serial.print(accel);
  Serial.print(" M=");        Serial.println(mag);
  Serial.println("---");
  delay(500);
}
