import cv2
import os

def check_camera(index):
    """对指定索引的摄像头进行深度体检"""
    # 使用 CAP_V4L2 后端
    cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
    
    if not cap.isOpened():
        return None

    # 尝试注入你期望的高速参数
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 200)

    # 读取实际参数
    actual_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    actual_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    actual_fps = cap.get(cv2.CAP_PROP_FPS)
    actual_fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
    fourcc_str = "".join([chr((actual_fourcc >> 8 * i) & 0xFF) for i in range(4)])

    cap.release()
    
    return {
        "index": index,
        "format": fourcc_str,
        "resolution": f"{int(actual_w)}x{int(actual_h)}",
        "fps": actual_fps
    }

def scan_all_cameras():
    print("🔍 正在扫描系统中的所有摄像头设备...")
    
    # 1. 获取所有 /dev/video* 节点
    video_devices = [int(f.replace('video', '')) for f in os.listdir('/dev') 
                     if f.startswith('video') and f[5:].isdigit()]
    video_devices.sort()

    if not video_devices:
        print("❌ 未在 /dev/ 下发现任何 video 设备节点。请检查 VMware USB 连接！")
        return

    found_any = False
    for idx in video_devices:
        report = check_camera(idx)
        
        if report:
            found_any = True
            print(f"\n[ 📷 摄像头设备 /dev/video{idx} ]")
            print(f"  - 实际视频格式: {report['format']}")
            print(f"  - 实际分辨率:   {report['resolution']}")
            print(f"  - 系统报告FPS:  {report['fps']}")
            
            # 特别提醒：Linux 下很多奇数号节点不吐画面，属于正常现象
            if report['resolution'] == "0x0":
                print("  ⚠️ 提示: 该节点可能仅用于传输元数据（Metadata），无图像流。")
        else:
            # 这里的节点可能是被占用或无权访问
            pass

    if not found_any:
        print("\n无法打开任何摄像头。请尝试执行: sudo chmod 777 /dev/video*")

if __name__ == "__main__":
    scan_all_cameras()