// CANSAT Duck2Dragon — LittleFS Remove Tool
// Target: TTGO SX1276 LoRa32 (ESP32)
// Function: Delete /cansat.csv from LittleFS internal flash.
//           Use after downloading data to free space for next flight.
// Usage:    Flash this sketch, open Serial Monitor at 115200.
//           Send 'Y' to confirm deletion. Any other key cancels.

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
    Serial.println("# LittleFS mount FAIL");
    return;
  }

  if (!LittleFS.exists(LOG_PATH))
  {
    Serial.print("# File not found: ");
    Serial.println(LOG_PATH);
    LittleFS.end();
    return;
  }

  // Show file size before asking
  File f = LittleFS.open(LOG_PATH, "r");
  size_t sz = f ? f.size() : 0;
  if (f) f.close();

  Serial.print("# Found: ");
  Serial.print(LOG_PATH);
  Serial.print("  size=");
  Serial.print(sz);
  Serial.println(" bytes");
  Serial.println("# Send 'Y' to DELETE, any other key to cancel.");
}

void loop()
{
  if (!Serial.available()) return;

  char c = Serial.read();
  if (c == 'Y' || c == 'y')
  {
    if (LittleFS.remove(LOG_PATH))
    {
      Serial.print("# DELETED: ");
      Serial.println(LOG_PATH);
    }
    else
    {
      Serial.println("# Delete FAILED");
    }
    LittleFS.end();
    Serial.println("# Done. Flash ready for next flight.");
  }
  else
  {
    Serial.println("# Cancelled. File not deleted.");
    LittleFS.end();
  }

  while (true) delay(1000);
}
