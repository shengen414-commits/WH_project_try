#pragma once
#include <map>
#include <string>
#include <cstdint>
#include <cstddef>

inline std::map<std::string, int>& fakePreferences() { static std::map<std::string, int> values; return values; }
inline bool& fakePreferenceFailure() { static bool fail = false; return fail; }
class Preferences {
    std::string prefix;
    bool readonly = true;
public:
    bool begin(const char* name, bool readOnly = false) { prefix = std::string(name) + "/"; readonly = readOnly; return true; }
    void end() {}
    int getInt(const char* key, int fallback = 0) {
        auto value = fakePreferences().find(prefix + key);
        return value == fakePreferences().end() ? fallback : value->second;
    }
    size_t putInt(const char* key, int value) {
        if (readonly || fakePreferenceFailure()) return 0;
        fakePreferences()[prefix + key] = value;
        return sizeof(int32_t);
    }
};
