/* * 汽车视觉实验：ESP32 高精度硬件同步触发器
 * 目标：产生 200Hz 极其稳定的方波给双相机
 */

const int TRIGGER_PIN = 17; // 连接到两台相机的 TRG
const int FREQ = 200;      // 200Hz -> 每 5ms 触发一次
const int PULSE_WIDTH_US = 100; // 脉冲宽度 100微秒，确保工业相机能感应到

hw_timer_t * timer = NULL;

// 定时器中断回调函数
void IRAM_ATTR onTimer() {
  digitalWrite(TRIGGER_PIN, HIGH);
  delayMicroseconds(PULSE_WIDTH_US);
  digitalWrite(TRIGGER_PIN, LOW);
}

void setup() {
  pinMode(TRIGGER_PIN, OUTPUT);
  digitalWrite(TRIGGER_PIN, LOW);

// 1. timerBegin 现在直接接收定时器频率 (Hz)。
// 原来 80 分频代表 1MHz (1,000,000 Hz)
timer = timerBegin(1000000); 

// 2. timerAttachInterrupt 现在只需要 2 个参数，去掉了触发边缘参数
timerAttachInterrupt(timer, &onTimer); 

// 3. 替代 timerAlarmWrite 和 timerAlarmEnable。
// 参数：(定时器对象, 触发计数值, 是否自动重载, 重载计数值通常为0)
timerAlarm(timer, 5000000 / FREQ, true, 0);
}

void loop() {
  // loop 留空，所有触发逻辑都在硬件中断里完成，不受系统干扰
}