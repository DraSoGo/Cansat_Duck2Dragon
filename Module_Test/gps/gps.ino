#include <TinyGPS++.h>

#define GPS_RX     16   // ESP32 receives on GPIO 16
#define GPS_TX     17   // ESP32 transmits on GPIO 17
#define GPS_BAUD   9600

HardwareSerial GPSserial(1);
TinyGPSPlus gps;

void setup()
{
  Serial.begin(115200);
  while (!Serial);
  GPSserial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);
  Serial.println("GPS test starting (waiting for fix)");
}

void loop()
{
  while (GPSserial.available())
  {
    gps.encode(GPSserial.read());
  }

  static uint32_t last = 0;
  if (millis() - last >= 1000)
  {
    last = millis();

    Serial.print("Sats=");
    Serial.print(gps.satellites.isValid() ? gps.satellites.value() : 0);
    Serial.print("\tFix=");
    Serial.print(gps.location.isValid() ? "YES" : "NO");

    if (gps.location.isValid())
    {
      Serial.print("\tLat=");  Serial.print(gps.location.lat(), 6);
      Serial.print("\tLon=");  Serial.print(gps.location.lng(), 6);
      Serial.print("\tAlt=");  Serial.print(gps.altitude.meters(), 2);
    }
    Serial.println();
  }
}
