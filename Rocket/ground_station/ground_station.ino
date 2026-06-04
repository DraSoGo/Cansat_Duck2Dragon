// CANSAT Duck2Dragon — Ground Station
// Target: TTGO SX1276 LoRa32 (ESP32)
// Function: Receive LoRa CSV from CanSat, forward to PC over USB Serial.

#include <SPI.h>
#include <LoRa.h>

#define LORA_SCK   5
#define LORA_MISO  19
#define LORA_MOSI  27
#define LORA_SS    18
#define LORA_RST   23     // Ground station uses 23 (per reference)
#define LORA_DIO0  26

#define BAND       922250000
#define LORA_BW    125E3
#define LORA_SF    11

void setup()
{
  Serial.begin(115200);
  while (!Serial);

  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_SS);
  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);

  if (!LoRa.begin(BAND))
  {
    Serial.println("# LoRa init FAIL");
    while (1);
  }
  LoRa.setSignalBandwidth(LORA_BW);
  LoRa.setSpreadingFactor(LORA_SF);

  Serial.println("# Ground Station ready");
  LoRa.receive();
}

void loop()
{
  int packetSize = LoRa.parsePacket();
  if (packetSize > 0)
  {
    // Print raw CSV line as received
    while (LoRa.available())
    {
      Serial.write((char)LoRa.read());
    }
    Serial.println();  // Add newline after CSV
    // Append RSSI/SNR as a comment line so Python logger sees it
    // but the CSV line above stays parseable as 23 fields.
    Serial.print("# RSSI=");
    Serial.print(LoRa.packetRssi());
    Serial.print(" SNR=");
    Serial.println(LoRa.packetSnr(), 2);
    Serial.flush();

    LoRa.receive();
  }
}
