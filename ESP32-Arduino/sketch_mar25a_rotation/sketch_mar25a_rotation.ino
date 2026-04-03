// 定义编码器引脚 (对应 ESP32 上的 D18 和 D19)
const int encoderPinA = 13; 
const int encoderPinB = 12; 

// 记录脉冲数，使用 volatile 确保中断里修改的值能被主循环正确读取
volatile long pulseCount = 0; 

// 控制打印频率
long lastPrintTime = 0;

// --- 中断服务程序 (ISR) ---
// IRAM_ATTR 是 ESP32 特有的，用于将中断函数放入 RAM 以提高响应速度并防止崩溃
void IRAM_ATTR updateEncoder() {
  // 当A相上升沿触发时，检查B相状态判断方向
  if (digitalRead(encoderPinB) == HIGH) {
    pulseCount++; // 正转
  } else {
    pulseCount--; // 反转
  }
}

void setup() {
  Serial.begin(115200); // ESP32 常用 115200 波特率
  Serial.println("ESP32 霍尔编码器测试开始...");

  // 设置引脚为输入，并开启内部上拉电阻
  pinMode(encoderPinA, INPUT_PULLUP);
  pinMode(encoderPinB, INPUT_PULLUP);

  // 绑定中断：引脚 D18，触发函数 updateEncoder，触发条件 RISING (上升沿)
  attachInterrupt(digitalPinToInterrupt(encoderPinA), updateEncoder, RISING);
}

void loop() {
  // 每 200 毫秒通过串口打印一次
  if (millis() - lastPrintTime > 200) {
    // 更改了前缀，避免带有数字
    Serial.print("Pos:");
    Serial.println(pulseCount);
    lastPrintTime = millis();
  }
}