#include "buzzer.h"
#include "alert_service.h"

void setup() {
  Serial.begin(115200);
  buzzerBegin();
  alertServiceBegin();
}

void loop() {
  alertServicePoll();
  delay(20);
}
