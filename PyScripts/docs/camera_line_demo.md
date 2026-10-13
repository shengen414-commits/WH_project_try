# OV9281 车前黑胶带中线 demo

## 当前项目与这次范围

依据当前主项目和「评估移除 ESP32 SD 卡存储模块」聊天的最新进展：

- ESP32 SD 存储已经拆除，编码器经串口上报，Orange Pi 保存记录。
- 网页后台已有相机预览/记录、车速和 MPS4264 压力同步记录与历史图。
- 正常固件已有 D12 舵机、八路巡线自动转向第一版（N1/N0），包括 PID、速率限制和丢线/超时故障锁定；尚不能因此宣称实车高速稳定。
- MPU6050 测试正在另一聊天推进。主项目已预留角速度反馈、外部目标角速度接口；摄像头转向适配器尚未实现。
- 八路巡线极性曾与实测 CSV 不一致，这个相机 demo 独立读取图像，不依赖该模块极性。

本次新增 `camera_line_detector.py`、`camera_line_demo.py`、独立网页模板和测试。demo 输出视觉测量，供后续控制接入；不会打开 ESP32 串口或发送转向/油门。使用相机时要退出占用同一 /dev/video 节点的主后台，或选择独立相机。

## 运行

在 Orange Pi 的仓库根目录、现有 venv 中运行：

```bash
python PyScripts/camera_line_demo.py --camera /dev/video0 --width 640 --height 480 --fps 120
```

电脑浏览器打开 `http://OrangePi的IP:5080`。默认 5080，与主后台 5000 分开。浏览器能显示叠加图、候选掩码和检测数值，SSH/无桌面可用；不需要 cv2.imshow。依赖 numpy、opencv-python-headless、flask，现有 requirements.txt 已包括。如缺依赖：

```bash
python -m pip install numpy opencv-python-headless flask
```

若摄像头不是 video0，先查设备及其支持的帧率/格式：

```bash
v4l2-ctl --list-devices
v4l2-ctl -d /dev/video0 --list-formats-ext
v4l2-ctl -d /dev/video0 --list-ctrls
```

v4l2-ctl 由系统 v4l-utils 包提供。需要选图像采集节点，不能选相机的元数据节点。也可以用 `/dev/v4l/by-id/` 下的稳定设备路径。

程序默认请求 MJPG。按实际列出的格式改用 `--fourcc YUYV` 或 `--fourcc GREY`，帧率也以 `--list-formats-ext` 对应分辨率的结果为准。启动会打印协商后的尺寸、格式、驱动报告帧率；网页另外显示实测采集和检测 FPS。OV9281 是单色全局快门传感器，厂商列出的传感器规格为 1280×800@120fps、VGA@180fps（MIPI/DVP），不能直接等同于你这块 USB 转接板的可用模式，也不能据现有后台的 210fps 请求推断真实帧率。

Windows 也能检查普通 USB 相机：

```powershell
python PyScripts/camera_line_demo.py --camera 0 --backend any --fps 60
```

没有相机时只测试网页和算法（醒目标注模拟）：

```bash
python PyScripts/camera_line_demo.py --demo
```

Ctrl+C 退出。网页的“下载当前标注图”保存快照到电脑。程序不连续写图像，避免磁盘写入影响检测；可检查保存的原始相机照片：

```bash
python PyScripts/camera_line_demo.py --image 路径/相机照片.png --output Car_Records/Vision_Debug/检查1
```

输出 overlay.png、mask.png、detection.json。输出目录相同会替换这三个文件，比较多个样本时用不同目录。

## 识别方法

1. 单色图缩到 320 像素宽，使用图像下部的地面区域（默认 y=35%～95%，x=4%～96%）。
2. 小核高斯去噪，反向灰度阈值找暗像素。默认 Otsu 自动阈值；木板纹理/阴影多时可切固定阈值或局部自适应。
3. 在 18 个近远采样带内找连续暗条，要求宽度合理、两侧可见且较亮。每带最多保留六个候选，排除宽阴影和大面积黑物。
4. 从跨越较远距离的中心点对生成直线假设，每带最多支持一个点，按覆盖、纵向跨度和残差评分。用支持点重新拟合 x=a*y+b。
5. 两条明显分开的线支持度接近、可见长度不足、拟合误差大时标记无效，不把无反馈当作零偏差。

这是用于**单条直线黑胶带**的轻量几何方法。直线的透视投影仍是直线，因此第一版不强行使用未经标定的鸟瞰变换。明显曲线、交叉口、相似黑线、低对比、过曝、强镜头畸变会超出当前方法范围。若需要地面厘米和航向角，后续应标定镜头畸变、相机外参和地面单应矩阵。

## 看图与调参

- 绿线：检测到的胶带中心线。黄色点：支持点，红色小点：所有候选。
- 蓝色竖线：期望中心（默认图像 x=50%）。绿圈：近端观测点；紫圈：前视点。
- 掩码中白色代表候选黑胶带，黑色代表背景。
- `near_error_norm`：近端中线减目标中心，再除以半幅图像宽度。线在图像右侧为正。
- `lookahead_error_norm`：前视点的相同偏差，反映前方线的位置。
- `image_angle_deg`：胶带朝图像右前方倾斜为正。**这是图像方向角，不是车辆横摆角或真实航向角**。
- `confidence`：覆盖与残差组成的启发式质量分数，不是识别正确率的统计概率。

先将车轮摆正、车辆与胶带平行，让画面中胶带处于预期路线，把目标中心调到正确 x 比例；镜头未居中时不一定是 0.5。ROI 应只包含车前地面，排除车身、车轮、远处墙面和大片黑物。前视位置是 ROI 内从上到下的比例，默认 0.4；越小看得越远，前提是该处胶带还足够清晰。

若掩码把木板大片选中，降低固定阈值或尝试自适应；若胶带没有被选中，提高固定阈值。网页参数只保存在当前进程，复启后可用命令行固定参数：

```bash
python PyScripts/camera_line_demo.py --camera /dev/video0 --threshold-mode fixed --threshold 65 --roi-top 0.4 --target-x 0.52
```

胶带过粗/细可调整 `--max-width 0.22`、`--min-width 0.005`（相对于处理图宽）。`--min-contrast` 是胶带相对两侧背景的最低灰度差。

## 后续高速走直线的控制方案

建议让相机负责预瞄路线，ESP32 负责时间确定的舵机控制；MPU6050 验证后用于角速度反馈。仅有近端横向误差时，车辆可能靠来回摆动纠正位置；结合前方偏移/方向与陀螺仪阻尼更适合继续研究高速直行。

后续需做：

1. 用实拍不同照明、偏移、速度的画面验证检出率；固定相机与地面姿态，标定目标中心、机械中位、左右方向和 PWM→转角。
2. 在地面标定后把近端横向误差与前视路线方向转为外环转向目标，可使用基于预瞄的 Pure Pursuit 或横向/方向复合控制；预瞄距离随车速增加。不能把当前图像角直接当作 deg/s 发送给 `@Y`。
3. MPU6050 角速度内环可提供摆动阻尼，编码器提供车速和低速/倒车条件。控制应与现有后台共享串口，继续保留 RC 接管和急停。
4. 为图像断流、检测歧义、串口超时设置有效标记与降速/停车；外环不能用旧画面或旧控制目标无限续命。
5. 在闭环测试中测量从曝光到舵机动作的总延迟。demo 的帧龄仅是 OpenCV read 返回至查询时的主机时间，**不包含曝光、USB、驱动队列和机械响应**。

全局快门有助于避免滚动快门形变，但不能消除曝光期间的运动模糊。高速时应根据相机实际 `--list-ctrls` 输出固定曝光/增益，配合足够照明、避免胶带过曝反光；控制名称及数值单位由 USB 板/驱动决定，不在 demo 里强行写入。前视距离必须覆盖相机、计算、串口和舵机的总延迟；例如 5m/s 时 50ms 的总延迟意味着车辆已经前进 25cm，这个量级不能只靠提高网页 FPS 解决。

采集线程持续读帧、单槽覆盖旧帧；检测频率与浏览器预览频率分离。网页的“跳过旧帧”是主动保持最新画面的统计，不等于相机丢帧。默认浏览器 12fps，检测上限 120fps；实际检测速度以 Orange Pi 实测为准。PC 合成图测试不是实车高速验证。

## 参考资料与取舍

- [DonkeyCar LineFollower 源码](https://github.com/autorope/donkeycar/blob/main/donkeycar/parts/line_follower.py)：横向切片、颜色阈值、目标像素和 PID；它的 HSV 黄线阈值不适合直接用在 OV9281 的单色黑胶带上。
- [OpenMV 黑线多 ROI 示例](https://github.com/openmv/openmv/blob/master/scripts/examples/02-Image-Processing/02-Color-Tracking/black_grayscale_line_following.py)：多个区域中心及加权偏差。
- [ROS line follower](https://github.com/chrisdalke/ros-line-follower-robot)：阈值、透视准备、视觉/控制分离；其作者也明确指出颜色/照明的适用限制。
- [OpenCV black-line bot](https://github.com/sainareshlingaxd/Line-Following-OPEN-CV-based-BOT)：黑线反阈值、ROI、浏览器 MJPEG 调试；其树莓派 CSI/ROS 启动方式不直接用于 Orange Pi 的 V4L2。
- [OriginBot 视觉巡线](https://www.originbot.org/en/application/cv_line_follower.html)：黑线、车前地面视角与 ROI。
- [OpenCV 阈值文档](https://docs.opencv.org/4.x/d7/d4d/tutorial_py_thresholding.html)、[形态学文档](https://docs.opencv.org/4.x/d9/d61/tutorial_py_morphological_ops.html)。
- [OV9281 官方资料](https://www.ovt.com/products/ov9281/)。

实现采用上述思路，代码为本项目独立编写，不依赖 ROS、训练模型或额外深度学习运行时。

测试：`python -X utf8 -m unittest discover -s PyScripts/tests -p test_camera_line_demo.py -v`。
