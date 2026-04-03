// ==========================================
// 1. 霍尔编码器配置 (传动轴)
// ==========================================
const int encoderPinA = 12; 
const int encoderPinB = 13; 
volatile long pulseCount = 0; 

// ==========================================
// 2. 新霍尔编码器配置 (车轮)
// ==========================================
const int wheelEncoderPinA = 22; // 车轮编码器A相 (原红外引脚)
const int wheelEncoderPinB = 21; // 车轮编码器B相 (新增)
volatile long wheelCount = 0;    // 记录轮子脉冲数 (现在可以区分正反转了)

// 控制打印频率
long lastPrintTime = 0;

// --- 传动轴霍尔编码器中断服务函数 ---
void IRAM_ATTR updateEncoder() {
  if (digitalRead(encoderPinB) == HIGH) {
    pulseCount++; 
  } else {
    pulseCount--; 
  }
}

// --- 车轮霍尔编码器中断服务函数 ---
void IRAM_ATTR updateWheelEncoder() {
  if (digitalRead(wheelEncoderPinB) == HIGH) {
    wheelCount++; 
  } else {
    wheelCount--; 
  }
}

void setup() {
  Serial.begin(115200); 
  Serial.println("ESP32 双霍尔编码器测试开始...");

  // 初始化传动轴霍尔编码器引脚及中断
  pinMode(encoderPinA, INPUT_PULLUP);
  pinMode(encoderPinB, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(encoderPinA), updateEncoder, RISING);

  // 初始化车轮霍尔编码器引脚及中断
  pinMode(wheelEncoderPinA, INPUT_PULLUP);
  pinMode(wheelEncoderPinB, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(wheelEncoderPinA), updateWheelEncoder, RISING);
}

void loop() {
  if (millis() - lastPrintTime > 200) {
    // 采用新格式输出： Data:传动轴脉冲,车轮脉冲
    Serial.print("Data:");
    Serial.print(pulseCount);
    Serial.print(",");
    Serial.println(wheelCount);
    lastPrintTime = millis();
  }
}