// ==========================================
// 1. 霍尔编码器配置 (传动轴)
// ==========================================
const int encoderPinA = 12; 
const int encoderPinB = 13; 
volatile long pulseCount = 0; 

// ==========================================
// 2. TCRT5000 红外传感器配置 (车轮)
// ==========================================
const int tcrtPin = 22; // 循迹模块 D0 接 D23
volatile long wheelCount = 0; // 记录轮子转的圈数
volatile unsigned long lastTcrtTime = 0; // 用于防抖的计时器

// 控制打印频率
long lastPrintTime = 0;

// --- 霍尔编码器中断服务函数 ---
void IRAM_ATTR updateEncoder() {
  if (digitalRead(encoderPinB) == HIGH) {
    pulseCount++; 
  } else {
    pulseCount--; 
  }
}

// --- TCRT5000 中断服务函数 ---
void IRAM_ATTR updateWheel() {
  unsigned long currentTime = millis();
  // 50毫秒软件防抖：两次触发间隔必须大于50ms，防止边缘毛刺导致多次计数
  if (currentTime - lastTcrtTime > 50) {
    // 因为正转反转都会经过黑胶布，这里只做简单的累加（绝对圈数）
    wheelCount++; 
    lastTcrtTime = currentTime;
  }
}

void setup() {
  Serial.begin(115200); 
  Serial.println("ESP32 双传感器测试开始...");

  // 初始化霍尔编码器引脚及中断
  pinMode(encoderPinA, INPUT_PULLUP);
  pinMode(encoderPinB, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(encoderPinA), updateEncoder, RISING);

  // 初始化 TCRT5000 引脚及中断 (RISING代表从亮到灭，即碰到黑胶布瞬间触发)
  pinMode(tcrtPin, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(tcrtPin), updateWheel, RISING);
}

void loop() {
  if (millis() - lastPrintTime > 200) {
    // 采用新格式输出： Data:电机脉冲,轮子圈数
    Serial.print("Data:");
    Serial.print(pulseCount);
    Serial.print(",");
    Serial.println(wheelCount);
    lastPrintTime = millis();
  }
}