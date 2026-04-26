// CANSAT Duck2Dragon — Deployment Subsystem
// Target: Arduino Nano (ATmega328P)
// Function: MS5611 barometric apogee detection -> servo parachute release.

#include <Wire.h>
#include <Servo.h>
#include <MS5611.h>

// ----- Adjustable parameters (TUNE THESE) -----
#define SERVO_PIN              9       // Servo PWM pin
#define SERVO_LOCKED_ANGLE     25      // Pre-deploy / locked position
#define SERVO_DEPLOY_ANGLE     120     // Released / deployed position
#define SERVO_SWEEP_DELAY_MS   3       // Delay between degree steps during release sweep

#define ARM_ALTITUDE_M         10.0    // Must reach this AGL altitude before apogee logic arms
#define APOGEE_DROP_M          5.0     // Altitude must drop this many meters below max
#define APOGEE_CONFIRM_COUNT   5       // Consecutive descending readings required
#define BASELINE_SAMPLES       20      // Samples averaged at startup for ground pressure

#define LED_PIN                13      // Built-in LED
#define LOOP_DELAY_MS          100     // ~10 Hz altitude sampling

// ----- State machine -----
enum State { IDLE, ASCENDING, DEPLOYED };
State state = IDLE;

MS5611 ms5611(0x77);
Servo  myservo;

float baselinePressure = 0;
float maxAltitude      = 0;
uint8_t confirmCount   = 0;

float readAltitude()
{
  if (ms5611.read() != MS5611_READ_OK) return NAN;
  float p = ms5611.getPressure();
  // Hypsometric formula relative to baseline
  return 44330.0 * (1.0 - pow(p / baselinePressure, 0.1903));
}

void deployParachute()
{
  Serial.println("APOGEE -> deploy");
  for (int pos = SERVO_LOCKED_ANGLE; pos <= SERVO_DEPLOY_ANGLE; pos++)
  {
    myservo.write(pos);
    delay(SERVO_SWEEP_DELAY_MS);
  }
  digitalWrite(LED_PIN, HIGH);
  state = DEPLOYED;
}

void setup()
{
  Serial.begin(9600);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  myservo.attach(SERVO_PIN);
  myservo.write(SERVO_LOCKED_ANGLE);

  Wire.begin();
  if (!ms5611.begin())
  {
    Serial.println("MS5611 init FAIL");
    while (1) { digitalWrite(LED_PIN, !digitalRead(LED_PIN)); delay(100); }
  }
  ms5611.setOversampling(OSR_HIGH);

  // Average baseline pressure
  Serial.println("Calibrating baseline pressure...");
  float sum = 0;
  uint8_t got = 0;
  while (got < BASELINE_SAMPLES)
  {
    if (ms5611.read() == MS5611_READ_OK)
    {
      sum += ms5611.getPressure();
      got++;
    }
    delay(50);
  }
  baselinePressure = sum / BASELINE_SAMPLES;

  Serial.print("Baseline P = "); Serial.print(baselinePressure, 2); Serial.println(" hPa");
  Serial.println("Deployment armed: state=IDLE");
}

void loop()
{
  static uint32_t lastBlink = 0;

  float alt = readAltitude();
  if (isnan(alt))
  {
    Serial.println("MS5611 read err");
    delay(LOOP_DELAY_MS);
    return;
  }

  switch (state)
  {
    case IDLE:
      if (alt > ARM_ALTITUDE_M)
      {
        state = ASCENDING;
        maxAltitude = alt;
        Serial.println("State: ASCENDING");
      }
      break;

    case ASCENDING:
      if (alt > maxAltitude) maxAltitude = alt;
      if (alt < (maxAltitude - APOGEE_DROP_M))
      {
        confirmCount++;
        if (confirmCount >= APOGEE_CONFIRM_COUNT)
        {
          deployParachute();
        }
      }
      else
      {
        confirmCount = 0;   // Reset noise
      }

      // Slow blink while armed
      if (millis() - lastBlink > 500)
      {
        digitalWrite(LED_PIN, !digitalRead(LED_PIN));
        lastBlink = millis();
      }
      break;

    case DEPLOYED:
      // One-shot: do nothing further
      break;
  }

  Serial.print("alt=");    Serial.print(alt, 2);
  Serial.print("\tmax=");  Serial.print(maxAltitude, 2);
  Serial.print("\tcnt=");  Serial.print(confirmCount);
  Serial.print("\tst=");   Serial.println(state);

  delay(LOOP_DELAY_MS);
}
