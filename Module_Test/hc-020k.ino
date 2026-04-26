// HC-020K photo-encoder pulse counter (autogyro RPM measurement)
// Pulses per revolution depends on encoder disc slots — adjust PPR accordingly.

#define HC020K_PIN  33      // Interrupt-capable GPIO on ESP32
#define PPR         20      // Pulses per revolution (slots in encoder disc)

volatile uint32_t pulseCount = 0;
uint32_t lastReport = 0;
uint32_t lastCount  = 0;

void IRAM_ATTR pulseISR()
{
  pulseCount++;
}

void setup()
{
  Serial.begin(115200);
  while (!Serial);
  pinMode(HC020K_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(HC020K_PIN), pulseISR, RISING);
  Serial.println("HC-020K test: spin encoder wheel");
  lastReport = millis();
}

void loop()
{
  uint32_t now = millis();
  if (now - lastReport >= 1000)
  {
    noInterrupts();
    uint32_t cur = pulseCount;
    interrupts();

    uint32_t delta = cur - lastCount;
    lastCount = cur;

    float rps = (float)delta / PPR;
    float rpm = rps * 60.0;

    Serial.print("Pulses=");  Serial.print(delta);
    Serial.print("\tTotal="); Serial.print(cur);
    Serial.print("\tRPM=");   Serial.println(rpm, 1);

    lastReport = now;
  }
}
