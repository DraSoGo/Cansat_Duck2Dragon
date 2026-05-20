#include <SPI.h>
#include <SD.h>

// HSPI pins (separate from LoRa VSPI)
#define SD_CS    13
#define SD_MOSI  15
#define SD_MISO  2
#define SD_CLK   4

SPIClass hspi(HSPI);

void setup()
{
  Serial.begin(115200);
  while (!Serial);

  hspi.begin(SD_CLK, SD_MISO, SD_MOSI, SD_CS);
  if (!SD.begin(SD_CS, hspi))
  {
    Serial.println("SD init failed");
    while (1) delay(1000);
  }
  Serial.print("SD type: ");
  switch (SD.cardType())
  {
    case CARD_MMC:  Serial.println("MMC");  break;
    case CARD_SD:   Serial.println("SD");   break;
    case CARD_SDHC: Serial.println("SDHC"); break;
    default:        Serial.println("UNKNOWN");
  }
  Serial.print("SD size: "); Serial.print(SD.cardSize() / (1024 * 1024));
  Serial.println(" MB");

  // Write
  File f = SD.open("/test.txt", FILE_WRITE);
  if (!f) { Serial.println("Open for write failed"); while (1); }
  f.println("CANSAT Duck2Dragon SD test");
  f.println(millis());
  f.close();
  Serial.println("Wrote /test.txt");

  // Read back
  f = SD.open("/test.txt", FILE_READ);
  if (!f) { Serial.println("Open for read failed"); while (1); }
  Serial.println("--- File contents ---");
  while (f.available()) Serial.write(f.read());
  f.close();
  Serial.println("--- end ---");
}

void loop()
{
  delay(1000);
}
