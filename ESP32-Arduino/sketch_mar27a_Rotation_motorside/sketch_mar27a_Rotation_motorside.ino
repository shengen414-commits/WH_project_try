// 定义编码器引脚
const int encA_pin = 12; 
const int encB_pin = 13; 

// 记录脉冲数的变量 (因为在中断中使用，必须加 volatile 关键字)
volatile long encoderCount = 0;

// 上次打印脉冲数的时间
unsigned long lastTime = 0;

// A相中断服务函数
void IRAM_ATTR isrA() {
  // 读取A和B的当前状态
  bool stateA = digitalRead(encA_pin);
  bool stateB = digitalRead(encB_pin);
  
  // 根据AB相位的关系判断正反转
  if (stateA == stateB) {
    encoderCount++; // 正转
  } else {
    encoderCount--; // 反转
  }
}

// B相中断服务函数
void IRAM_ATTR isrB() {
  bool stateA = digitalRead(encA_pin);
  bool stateB = digitalRead(encB_pin);
  
  if (stateA != stateB) {
    encoderCount++; // 正转
  } else {
    encoderCount--; // 反转
  }
}

void setup() {
  Serial.begin(115200);
  
  // 初始化引脚模式为输入，并启用内部上拉电阻
  // 很多霍尔传感器是开漏输出，必须有上拉电阻才能读到高电平
  pinMode(encA_pin, INPUT_PULLUP);
  pinMode(encB_pin, INPUT_PULLUP);
  
  // 附加中断，CHANGE表示引脚电平发生变化（上升沿或下降沿）时触发
  attachInterrupt(digitalPinToInterrupt(encA_pin), isrA, CHANGE);
  attachInterrupt(digitalPinToInterrupt(encB_pin), isrB, CHANGE);
  
  Serial.println("编码器测试开始...");
}

void loop() {
  // 每隔500毫秒打印一次当前脉冲数
  if (millis() - lastTime >= 500) {
    lastTime = millis();
    
    // 为了防止在读取大整数时发生中断导致数据错乱，读取时暂时关闭中断
    noInterrupts();
    long currentCount = encoderCount;
    interrupts();
    
    Serial.print("当前脉冲计数: ");
    Serial.println(currentCount);
  }
}