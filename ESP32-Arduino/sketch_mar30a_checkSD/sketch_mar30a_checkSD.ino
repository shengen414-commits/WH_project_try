#include <SPI.h>
#include <SD.h>

const int chipSelect = 5;

void setup() {
  Serial.begin(115200);
  delay(1000); // 等待串口稳定
  Serial.println("\n--- SD卡硬件连线诊断开始 ---");

  if (!SD.begin(chipSelect)) {
    Serial.println("❌ 致命错误：找不到 SD 卡！");
    Serial.println("👉 请检查：1.VCC是否接了5V？ 2.引脚(5,18,19,23)是否插紧？ 3.卡是否插到底了？");
    return;
  }
  
  uint8_t cardType = SD.cardType();
  if (cardType == CARD_NONE) {
    Serial.println("❌ 错误：虽然连上了模块，但里面没有插卡！");
    return;
  }

  Serial.println("✅ SD卡初始化成功！");
  Serial.print("卡片类型: ");
  if (cardType == CARD_MMC) Serial.println("MMC");
  else if (cardType == CARD_SD) Serial.println("SDSC");
  else if (cardType == CARD_SDHC) Serial.println("SDHC");
  else Serial.println("未知");

  uint64_t cardSize = SD.cardSize() / (1024 * 1024);
  Serial.printf("卡片总容量: %llu MB\n", cardSize);
  
  // 测试能否打开根目录
  File root = SD.open("/");
  if (root) {
    Serial.println("✅ 根目录打开成功，文件系统(FAT32)正常！");
    root.close();
  } else {
    Serial.println("❌ 错误：可以读取硬件，但无法打开根目录！说明绝对是【格式化】的问题。");
  }
}

void loop() {}