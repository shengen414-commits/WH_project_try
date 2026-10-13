#pragma once
inline bool fakeValidGPIO(int pin) {
    return pin >= 0 && pin <= 39 && pin != 20 && pin != 24 && !(pin >= 28 && pin <= 31);
}
#define GPIO_IS_VALID_GPIO(pin) fakeValidGPIO(pin)
#define GPIO_IS_VALID_OUTPUT_GPIO(pin) (fakeValidGPIO(pin) && (pin) < 34)
