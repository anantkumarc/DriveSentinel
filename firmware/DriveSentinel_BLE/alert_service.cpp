#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include "alert_service.h"
#include "buzzer.h"

// Bluetooth SIG Immediate Alert Service and Alert Level characteristic.
static constexpr char SERVICE_UUID[] = "00001802-0000-1000-8000-00805f9b34fb";
static constexpr char LEVEL_UUID[] = "00002a06-0000-1000-8000-00805f9b34fb";
static constexpr unsigned long WATCHDOG_MS = 4000;
static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
static volatile uint8_t level = 0;
static volatile unsigned long updated = 0;
static BLEServer* server = nullptr;
static bool wasConnected = false;

class AlertCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* characteristic) override {
    if (characteristic->getLength() != 1) return;
    const uint8_t value = characteristic->getData()[0];
    if (value > 2) return;
    portENTER_CRITICAL(&mux);
    level = value;
    updated = millis();
    portEXIT_CRITICAL(&mux);
  }
};

void alertServiceBegin() {
  BLEDevice::init("DriveSentinel-Buzzer");
  server = BLEDevice::createServer();
  BLEService* service = server->createService(SERVICE_UUID);
  BLECharacteristic* alert = service->createCharacteristic(LEVEL_UUID, BLECharacteristic::PROPERTY_WRITE_NR);
  alert->setCallbacks(new AlertCallbacks());
  service->start();
  BLEAdvertising* advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(SERVICE_UUID);
  advertising->start();
  Serial.println("DriveSentinel BLE ready");
}

void alertServicePoll() {
  bool connected = server->getConnectedCount() > 0;
  portENTER_CRITICAL(&mux);
  uint8_t current = level;
  unsigned long last = updated;
  if (!connected) level = 0;
  portEXIT_CRITICAL(&mux);
  buzzerSet(connected && current != 0 && (millis() - last < WATCHDOG_MS));
  if (wasConnected && !connected) BLEDevice::startAdvertising();
  wasConnected = connected;
}
