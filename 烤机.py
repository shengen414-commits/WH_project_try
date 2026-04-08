import multiprocessing
import time
import math

def cpu_burner():
    """这是一个密集的浮点运算死循环，专门用来让 CPU 发热"""
    while True:
        # 不断进行浮点运算，最大化占用算力单元
        x = 9999.99
        x = math.sqrt(x * x)

if __name__ == '__main__':
    # 自动获取香橙派的 CPU 核心数
    cores = multiprocessing.cpu_count()
    print(f"🔥 检测到 {cores} 个 CPU 核心，准备开始烤机...")
    print("⚠️  警告：温度即将飙升！按 Ctrl+C 可随时停止测试。")
    time.sleep(2) # 给用户两秒钟的后悔时间

    processes = []
    try:
        # 为每一个 CPU 核心启动一个无情的运算进程
        for i in range(cores):
            p = multiprocessing.Process(target=cpu_burner)
            p.start()
            processes.append(p)

        # 主进程在此挂机，等待用户按 Ctrl+C
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n🛑 接收到停止信号，正在结束所有烤机进程...")
        for p in processes:
            p.terminate() # 强制杀死进程
            p.join()
        print("✅ 烤机已安全结束，CPU 正在冷却。")