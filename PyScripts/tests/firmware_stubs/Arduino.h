#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <cmath>
#include <algorithm>
#define IRAM_ATTR
constexpr int INPUT_PULLDOWN = 2;
constexpr int CHANGE = 3;
using std::min;
using std::max;
template <typename T> T constrain(T value, T low, T high) {
    return std::min(high, std::max(low, value));
}
constexpr uint8_t LOW = 0;
constexpr uint8_t HIGH = 1;

struct FakeSerial {
    std::string input;
    std::string output;
    int capacity = 128;
    int available() { return (int)input.size(); }
    char read() { char c = input.front(); input.erase(0, 1); return c; }
    int availableForWrite() { return capacity; }
    void print(const char* value) { output += value; }
    void print(int value) { output += std::to_string(value); }
    void println(const char* value) { print(value); output += '\n'; }
    void println(int value) { print(value); output += '\n'; }
    size_t write(const uint8_t* data, int length) {
        output.append((const char*)data, length);
        return length;
    }
};
extern FakeSerial Serial;
unsigned long millis();
unsigned long micros();
int digitalRead(int pin);
inline void pinMode(int, int) {}
inline int digitalPinToInterrupt(int pin) { return pin; }
inline void attachInterrupt(int, void (*)(), int) {}
