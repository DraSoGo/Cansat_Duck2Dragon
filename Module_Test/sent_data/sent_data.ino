#include <SPI.h>
#include <LoRa.h>

// ——— LoRa SPI pins (TTGO LoRa32 V1) ———
#define LORA_SCK   5
#define LORA_MISO  19
#define LORA_MOSI  27
#define LORA_SS    18
#define LORA_RST   14   // Use 23 if testing on Ground Station board
#define LORA_DIO0  26

#define BAND       922525000
#define LORA_BW    125E3
#define LORA_SF    9

uint32_t counter = 0;

void setup()
{
  Serial.begin(115200);
  while (!Serial);

  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_SS);
  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);

  if (!LoRa.begin(BAND))
  {
    Serial.println("LoRa init failed!");
    while (1);
  }
  LoRa.setSignalBandwidth(LORA_BW);
  LoRa.setSpreadingFactor(LORA_SF);

  Serial.println("LoRa TX ready");
}

void loop()
{
  LoRa.beginPacket();
  LoRa.print("Hello D2D #");
  LoRa.print(counter);
  LoRa.endPacket();

  Serial.print("Sent #");
  Serial.println(counter);
  counter++;
  delay(1000);
}
