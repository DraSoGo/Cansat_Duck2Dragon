#include <SPI.h>
#include <LoRa.h>

// ——— LoRa SPI pins (TTGO LoRa32 V1) ———
#define LORA_SCK   5
#define LORA_MISO  19
#define LORA_MOSI  27
#define LORA_SS    18
#define LORA_RST   23   // Ground Station board (use 14 on Rocket board)
#define LORA_DIO0  26

#define BAND       922525000
#define LORA_BW    125E3
#define LORA_SF    9

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

  LoRa.receive();
  Serial.println("LoRa RX ready");
}

void loop()
{
  int packetSize = LoRa.parsePacket();
  if (packetSize)
  {
    Serial.print("Recv: ");
    while (LoRa.available())
    {
      Serial.print((char)LoRa.read());
    }
    Serial.print(" | RSSI=");
    Serial.print(LoRa.packetRssi());
    Serial.print(" | SNR=");
    Serial.println(LoRa.packetSnr());
  }
}
