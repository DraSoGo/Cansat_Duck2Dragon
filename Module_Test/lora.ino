#include <SPI.h>
#include <LoRa.h>

// Toggle role: comment out one of the following.
#define MODE_TX
// #define MODE_RX

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

#ifdef MODE_TX
  Serial.println("LoRa TX mode");
#else
  Serial.println("LoRa RX mode");
  LoRa.receive();
#endif
}

void loop()
{
#ifdef MODE_TX
  LoRa.beginPacket();
  LoRa.print("Hello D2D #");
  LoRa.print(counter++);
  LoRa.endPacket();
  Serial.print("Sent #"); Serial.println(counter);
  delay(1000);
#else
  int packetSize = LoRa.parsePacket();
  if (packetSize)
  {
    Serial.print("Recv: ");
    while (LoRa.available()) Serial.print((char)LoRa.read());
    Serial.print(" RSSI="); Serial.print(LoRa.packetRssi());
    Serial.print(" SNR=");  Serial.println(LoRa.packetSnr());
  }
#endif
}
