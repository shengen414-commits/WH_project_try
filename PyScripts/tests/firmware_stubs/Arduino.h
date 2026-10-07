#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
constexpr uint8_t LOW = 0;
constexpr uint8_t HIGH = 1;

struct FakeSerial {
    std::string input;
    std::string output;
    int capacity = 128;
    int available() { return (int)input.size(); }
    char read() { char c = input.front(); input.erase(0, 1); return c; }
    int availableForWrite() { return capacity; }
    size_t write(const uint8_t* data, int length) {
        output.append((const char*)data, length);
        return length;
    }
};
extern FakeSerial Serial;
unsigned long millis();
