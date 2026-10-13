#pragma once

// 精简的位置式 PID 控制器(带抗积分饱和、误差微分)。
// 时间由 micros() 推导,因此 Ki/Kd 已经是"每秒"量纲(无需手动除以周期)。
class PID {
public:
    void begin(float kp, float ki, float kd, float outMin, float outMax);
    void setTunings(float kp, float ki, float kd);
    void setOutputLimits(float outMin, float outMax);
    void reset();

    // setpoint 与 measurement 使用同一量纲(如 deg/s),返回输出 ∈ [outMin, outMax]
    float compute(float setpoint, float measurement);

private:
    float kp_ = 0, ki_ = 0, kd_ = 0;
    float outMin_ = -500, outMax_ = 500;
    float integral_ = 0;
    float prevError_ = 0;
    unsigned long prevTimeUs_ = 0;
    bool first_ = true;
};
