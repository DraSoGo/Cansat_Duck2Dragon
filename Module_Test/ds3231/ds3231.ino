#include <Wire.h>
#include <RTClib.h>

#define I2C_SDA 21
#define I2C_SCL 22

RTC_DS3231 rtc;

void setup()
{
  Serial.begin(115200);
  while (!Serial);
  Wire.begin(I2C_SDA, I2C_SCL);

  if (!rtc.begin())
  {
    Serial.println("DS3231 not found");
    while (1) delay(1000);
  }

  if (rtc.lostPower())
  {
    Serial.println("RTC lost power, setting time to compile time");
    rtc.adjust(DateTime(F(__DATE__), F(__TIME__)));
  }

  // Force-set RTC to compile time (uncomment once, then re-comment and re-flash):
  // rtc.adjust(DateTime(F(__DATE__), F(__TIME__)));

  Serial.println("DS3231 OK");
}

void loop()
{
  DateTime now = rtc.now();

  char buf[32];
  snprintf(buf, sizeof(buf), "%04u-%02u-%02u %02u:%02u:%02u",
           now.year(), now.month(), now.day(),
           now.hour(), now.minute(), now.second());

  Serial.print("Time: ");   Serial.print(buf);
  Serial.print("\tTemp: "); Serial.print(rtc.getTemperature(), 2);
  Serial.println(" C");
  delay(1000);
}
