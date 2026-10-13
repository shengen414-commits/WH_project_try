#pragma once
#include <cstdint>
#include <cstddef>
#include <map>
#include <vector>
#include <deque>

struct FakeWire {
    std::map<uint8_t, std::map<uint8_t, uint8_t>> devices;
    std::vector<uint8_t> sample = std::vector<uint8_t>(14, 0);
    std::vector<uint8_t> tx;
    std::deque<uint8_t> rx;
    uint8_t address = 0, reg = 0;
    bool beginOK = true, open = false, shortRead = false, failRead = false, ignoreWrites = false;
    int failWriteRegister = -1, sda = -1, scl = -1, timeoutMs = 0, transactions = 0;
    uint32_t frequency = 0;
    bool begin(int data, int clock, uint32_t speed) { sda = data; scl = clock; frequency = speed; open = beginOK; return open; }
    void end() { open = false; }
    void setTimeOut(int timeout) { timeoutMs = timeout; }
    void beginTransmission(uint8_t addr) { address = addr; tx.clear(); }
    size_t write(uint8_t value) { tx.push_back(value); return 1; }
    uint8_t endTransmission(bool) {
        ++transactions;
        if (!devices.count(address)) return 2;
        if (!tx.empty()) reg = tx[0];
        if (tx.size() > 1) {
            if (reg == failWriteRegister) return 3;
            if (!ignoreWrites) devices[address][reg] = tx[1];
        }
        return 0;
    }
    uint8_t requestFrom(uint8_t addr, uint8_t count, uint8_t) {
        ++transactions; rx.clear();
        if (failRead || !devices.count(addr)) return 0;
        const uint8_t n = shortRead ? count - 1 : count;
        for (uint8_t i = 0; i < n; ++i) rx.push_back(reg == 0x3b ? sample[i] : devices[addr][reg + i]);
        return n;
    }
    int available() { return (int)rx.size(); }
    uint8_t read() { uint8_t v = rx.front(); rx.pop_front(); return v; }
};
extern FakeWire Wire;
