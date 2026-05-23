// CANSAT Duck2Dragon — LittleFS Download Tool
// Target: TTGO SX1276 LoRa32 (ESP32)
// Function: Dump /cansat.csv from LittleFS to Serial at 115200 baud.
//           Capture output in Serial Monitor -> Save to file on PC.
// Usage:    Flash this sketch, open Serial Monitor at 115200, wait for dump.

#include <LittleFS.h>

#define BAUD      115200
#define LOG_PATH  "/cansat.csv"

void setup()
{
  Serial.begin(BAUD);
  while (!Serial) delay(10);
  delay(500);

  if (!LittleFS.begin(false))
  {
    Serial.println("# LittleFS mount FAIL — do not format, data may be lost");
    return;
  }

  File f = LittleFS.open(LOG_PATH, "r");
  if (!f)
  {
    Serial.print("# File not found: ");
    Serial.println(LOG_PATH);
    LittleFS.end();
    return;
  }

  size_t total = f.size();
  Serial.print("# Downloading: ");
  Serial.print(LOG_PATH);
  Serial.print("  size=");
  Serial.print(total);
  Serial.println(" bytes");
  Serial.println("# --- BEGIN ---");

  while (f.available())
  {
    Serial.write(f.read());
  }

  Serial.println();
  Serial.println("# --- END ---");
  f.close();
  LittleFS.end();
  Serial.println("# Done.");
}

void loop() {}
