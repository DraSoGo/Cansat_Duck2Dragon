// CANSAT Duck2Dragon — Deployment Subsystem
// Target: Arduino Nano (ATmega328P)
// Function: MS5611 barometric apogee detection -> servo parachute release.

#include <Wire.h>
#include <Servo.h>
#include <MS5611.h>

// ----- Adjustable parameters (TUNE THESE) -----
#define SERVO_PIN              9       // Servo PWM pin
#define SERVO_LOCKED_ANGLE     35      // Pre-deploy / locked position
#define SERVO_DEPLOY_ANGLE     110     // Released / deployed position
#define SERVO_SWEEP_DELAY_MS   3       // Delay between degree steps during release sweep

#define ARM_ALTITUDE_M         50    // Must reach this AGL altitude before apogee logic arms
#define APOGEE_CONFIRM_COUNT   2       // Consecutive descending readings required
#define APOGEE_DEADBAND_M      0.5    // Ignore altitude changes smaller than this (noise filter)
#define BASELINE_SAMPLES       20      // Samples averaged at startup for ground pressure

#define LED_PIN                13      // Built-in LED
#define LOOP_DELAY_MS          100     // ~10 Hz altitude sampling

// ----- State machine -----
enum State { IDLE, ASCENDING, DEPLOYED };
State state = IDLE;

MS5611 ms5611(0x77);
Servo  myservo;

float time = 0.1; 
float baselinePressure = 0;
float prevAltitude     = 0;
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
  myservo.write(120);
  digitalWrite(LED_PIN, HIGH);
  state = DEPLOYED;
}

void setup()
{
  Serial.begin(9600);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  myservo.attach(SERVO_PIN);
  // myservo.write(0);
  // Serial.println("Servo Zero");
  myservo.write(120);
  delay(5000);
  myservo.write(SERVO_LOCKED_ANGLE);
  Serial.println("Servo Lock");
  delay(10000);

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
        prevAltitude = alt;
        Serial.println("State: ASCENDING");
      }
      break;

    case ASCENDING:
      // Detect descent with deadband — ignore noise < APOGEE_DEADBAND_M
      if (alt < prevAltitude - APOGEE_DEADBAND_M)
      {
        confirmCount++;
        if (confirmCount >= APOGEE_CONFIRM_COUNT)
        {
          deployParachute();
        }
      }
      else if (alt > prevAltitude + APOGEE_DEADBAND_M)
      {
        confirmCount = 0;   // Reset only on clear ascent, not noise
      }
      // Within deadband → keep confirmCount unchanged
      prevAltitude = alt;
      if (time >= 10)
      {
        time += 0.1;
        deployParachute();
      }
      // Slow blink while armed
      if (millis() - lastBlink > 500)
      {
        digitalWrite(LED_PIN, !digitalRead(LED_PIN));
        lastBlink = millis();
      }
      break;

    case DEPLOYED:
      delay(1000000);
      break;
      // One-shot: do nothing further
  }

  Serial.print("alt=");    Serial.print(alt, 2);
  Serial.print("\tprev="); Serial.print(prevAltitude, 2);
  Serial.print("\tcnt=");  Serial.print(confirmCount);
  Serial.print("\tst=");   Serial.println(state);

  delay(LOOP_DELAY_MS);
}
