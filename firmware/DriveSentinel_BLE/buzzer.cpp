#include "buzzer.h"
// GPIO4 on common ESP32-C3 SuperMini boards; check your board pinout.
static constexpr uint8_t BUZZER_GPIO = 4;
void buzzerBegin() {
  digitalWrite(BUZZER_GPIO, LOW);
  pinMode(BUZZER_GPIO, OUTPUT);
}
void buzzerSet(bool active) {
  digitalWrite(BUZZER_GPIO, active ? HIGH : LOW);
}
