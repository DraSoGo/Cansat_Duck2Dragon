// Target board: Arduino Nano (deployment board test)
#include <Servo.h>

#define SERVO_PIN           9
#define SERVO_LOCKED_ANGLE  0    // Locked / pre-deploy position
#define SERVO_DEPLOY_ANGLE  180   // Deployed position

Servo myservo;

void setup()
{
  Serial.begin(9600);
  myservo.attach(SERVO_PIN);
  // myservo.write(SERVO_LOCKED_ANGLE);
  // Serial.println("Servo test: LOCKED -> DEPLOY -> sweep");
  // delay(2000);

  // Serial.println("Move to DEPLOY angle");
  // for (int pos = SERVO_LOCKED_ANGLE; pos <= SERVO_DEPLOY_ANGLE; pos++)
  // {
  //   myservo.write(pos);
  //   delay(15);
  // }
  // delay(2000);

  // Serial.println("Return to LOCKED angle");
  // for (int pos = SERVO_DEPLOY_ANGLE; pos >= SERVO_LOCKED_ANGLE; pos--)
  // {
  //   myservo.write(pos);
  //   delay(15);
  // }
}

void loop()
{
  // for (int pos = 0; pos <= 5; pos++) { myservo.write(pos); delay(10); }
  // delay(100000);
  // for (int pos = 180; pos >= 0; pos--) { myservo.write(pos); delay(10); }
  // delay(100000);
}
