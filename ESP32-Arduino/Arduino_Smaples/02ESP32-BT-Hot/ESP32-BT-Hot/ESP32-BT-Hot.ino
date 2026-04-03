#include <BluetoothSerial.h>

BluetoothSerial SerialBT;

void setup() {
  Serial.begin(115200);
  SerialBT.begin("ESP32-BlueTooth"); //Bluetooth device name    
  Serial.println("The device started, now you can pair it with bluetooth!");
  // run high latency network code in 2nd core instead in loop()

}

void loop() {
  if (Serial.available()) {
    SerialBT.write(Serial.read());
  }
  if (SerialBT.available()) {
    Serial.write(SerialBT.read());
  }
  delay(20);
}
