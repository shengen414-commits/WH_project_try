#include <cassert>
#include <cmath>
#include <map>
#include "Arduino.h"
#include "Wire.h"
#include "mpu6050_debug.h"
#include "line_sensor.h"
#include "esc_control.h"
#include "serial_transport.h"
#include "automatic_steering.h"

FakeSerial Serial;
FakeWire Wire;
unsigned long clockMs = 10000;
bool servoAttachSucceeds = true;
std::map<int, int> pwmOutputs;
unsigned long millis() { return clockMs; }
unsigned long micros() { return clockMs * 1000; }
int digitalRead(int) { return LOW; }
long readEncoderPosition() { return 42; }
bool readLineSensorFrame(LineSensorFrame& frame) { frame = {1, (uint32_t)clockMs, 0x18, 0x18}; return true; }
void feed(const char* text) { Serial.input += text; pollSerialCommands(); }
void word(int offset, int16_t value) {
    Wire.sample[offset] = uint16_t(value) >> 8;
    Wire.sample[offset + 1] = uint16_t(value) & 255;
}
void tick(unsigned int times = 1) {
    for (unsigned int i = 0; i < times; ++i) {
        clockMs += 10; updateESC(); updateMPU6050Debug(); publishMPU6050Debug();
    }
}
void device(uint8_t address = 0x68, uint8_t who = 0x68) {
    Wire.devices.clear(); Wire.devices[address][0x75] = who;
    Wire.failRead = false; Wire.shortRead = false; Wire.ignoreWrites = false; Wire.failWriteRegister = -1;
    Wire.beginOK = true;
}

int main() {
    initESC();
    MPU6050Sample sample;
    const int before = Wire.transactions;
    tick(5); assert(Wire.transactions == before); // Normal mode has no IMU bus activity.
    assert(!readMPU6050Sample(sample));
    device();
    word(0, 16384); word(2, -8192); word(4, 16384); word(6, 3400);
    word(8, 655); word(10, -1310); word(12, 131);
    feed("T1800\nM\n");
    assert(Wire.sda == 16 && Wire.scl == 4 && Wire.frequency == 100000 && Wire.timeoutMs == 3);
    assert(fakePinModes()[17] == OUTPUT && fakePinLevels()[17] == LOW);
    assert(fakePinModes()[5] == INPUT);
    // GPIO4/16 now serve SCL/SDA, not the MPU auxiliary XCL/XDA interface.
    assert(pwmOutputs[14] == 1500 && !serialNormalMode());
    tick(45);
    assert(readMPU6050Sample(sample));
    assert(std::fabs(sample.axG - 1) < .001 && std::fabs(sample.ayG + .5f) < .001);
    assert(std::fabs(sample.azG - 1) < .001 && std::fabs(sample.temperatureC - 46.53f) < .001);
    assert(std::fabs(sample.gxDps - 10) < .001 && std::fabs(sample.gyDps + 20) < .001);
    assert(std::fabs(sample.gzDps - 2) < .001 && !sample.calibrated);
    Serial.output.clear();
    feed("T2000\nB1900\n");
    tick(20); publishEncoder(); publishLineSensor();
    assert(pwmOutputs[14] == 1500);
    assert(Serial.output.find("[MPU] a=") != std::string::npos);
    assert(Serial.output.find("[ENC]") == std::string::npos && Serial.output.find("[LINE]") == std::string::npos);
    assert(Serial.output.find("[DRIVE]") == std::string::npos);

    // Z is asynchronous and does not zero the raw gyro output.
    word(0, 0); word(2, 0); word(4, 16384); word(8, 0); word(10, 0);
    tick(); feed("z\n");
    tick(200);
    assert(readMPU6050Sample(sample) && sample.calibrated);
    assert(std::fabs(sample.gzDps - 2) < .001 && std::fabs(sample.correctedGzDps) < .001);
    feed("Z\n"); word(8, 655); tick();
    assert(Serial.output.find("ZERO rejected") != std::string::npos);
    assert(readMPU6050Sample(sample) && sample.calibrated); // Old good bias is preserved.
    word(8, 0);
    feed("Z\nE\nT1900\n");
    assert(Serial.input.empty() && pwmOutputs[14] == 1500);
    assert(Serial.output.find("ZERO canceled") != std::string::npos);
    // Read failures invalidate data; do not emit a fabricated zero frame.
    Wire.shortRead = true; Serial.output.clear(); tick();
    assert(!readMPU6050Sample(sample));
    assert(Serial.output.find("sample_read_failed") != std::string::npos);
    assert(Serial.output.find("[MPU] a=") == std::string::npos);
    Wire.shortRead = false; feed("M\n"); tick(45);
    assert(readMPU6050Sample(sample) && !sample.calibrated);
    clockMs += 101; assert(!readMPU6050Sample(sample)); // Age checked even if no driver call occurred.
    feed("R\n");
    const int closed = Wire.transactions; tick(5);
    assert(!Wire.open && Wire.transactions == closed && serialNormalMode());
    assert(!readMPU6050Sample(sample));
    Serial.output.clear(); publishEncoder(); publishLineSensor();
    assert(Serial.output.find("[ENC]") != std::string::npos && Serial.output.find("[LINE]") != std::string::npos);

    device(0x69); Serial.output.clear(); feed("M\n"); tick(45);
    assert(readMPU6050Sample(sample));
    assert(Serial.output.find("address=0x69 WHO_AM_I=0x68") != std::string::npos);
    device(0x68, 0x98); Serial.output.clear(); feed("M\n"); tick(45);
    assert(!readMPU6050Sample(sample));
    assert(Serial.output.find("WHO_AM_I=0x98") != std::string::npos);
    assert(Serial.output.find("WHO_AM_I_mismatch") != std::string::npos);
    device(); Wire.failWriteRegister = 0x1b; Serial.output.clear(); feed("M\n"); tick(45);
    assert(!readMPU6050Sample(sample));
    assert(Serial.output.find("configuration_write_failed") != std::string::npos);
    device(); Wire.ignoreWrites = true; Serial.output.clear(); feed("M\n"); tick(45);
    assert(!readMPU6050Sample(sample));
    assert(Serial.output.find("configuration_verify_failed") != std::string::npos);
    device(); Wire.devices.clear(); Serial.output.clear(); feed("M\n"); tick(45);
    assert(!readMPU6050Sample(sample));
    assert(Serial.output.find("no_response_at_0x68_or_0x69") != std::string::npos);
    device(); Wire.beginOK = false; Serial.output.clear(); feed("M\n"); tick();
    assert(Serial.output.find("i2c_begin_failed") != std::string::npos);
    feed("L\n"); assert(serialNormalMode() == false && !Wire.open);
    device(); feed("M\nN0\n"); assert(serialNormalMode() && !Wire.open);
    // Six-axis data remains diagnostic only; it has not armed the gyro PID.
    assert(!handleAutomaticSteeringConfig("H1"));
    device(); feed("o\n"); tick(45); Serial.output.clear();
    tick(10); publishEncoder(); publishLineSensor();
    assert(Serial.output.find("[IMU],") != std::string::npos);
    assert(Serial.output.find("[MPU] a=") == std::string::npos);
    assert(Serial.output.find("[ENC]") == std::string::npos && Serial.output.find("[LINE]") == std::string::npos);
    feed("T2000\nB1900\n"); tick(); assert(pwmOutputs[14] == 1500);
    Wire.shortRead = true; Serial.output.clear(); tick();
    assert(Serial.output.find("[IMU],") == std::string::npos && !readMPU6050Sample(sample));
    device(); feed("M\n"); tick(45); Serial.output.clear(); tick(20);
    assert(Serial.output.find("[MPU] a=") != std::string::npos);
    assert(Serial.output.find("[IMU],") == std::string::npos);
    feed("R\n"); assert(serialNormalMode() && !Wire.open);
}
