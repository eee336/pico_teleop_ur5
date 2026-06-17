# Quest UR5e Teleoperation

这个项目用 Meta Quest 浏览器里的 WebXR 读取手柄位姿，通过电脑上的 Python 网关转换成 UR5e 基座坐标系下的 TCP 目标位姿，再用 UR RTDE `servoL` 发送给机械臂。

默认启动是干跑模式，不会连接或移动真实机械臂。真机运动需要同时满足两件事：配置文件里 `robot.enabled: true`，并且启动命令带 `--real`。

## 项目结构

```text
quest_ur5e_teleop/
  app.py          FastAPI / WebSocket / 静态页面服务
  controller.py   遥操作状态机、标定、限速、工作空间夹紧
  recording.py    raw episode 采集器
  robot.py        SimRobot 与 UR RTDE 真机适配
  transforms.py   坐标轴映射、四元数、旋转向量转换
  tools/
    export_lerobot.py
web/
  index.html      Quest Browser 控制台
  app.js          WebXR 手柄位姿采集与 WebSocket 协议
  styles.css
config/
  teleop.example.yaml
scripts/
  make_self_signed_cert.sh
  train_smolvla.sh
docs/
  lerobot_smolvla.md
tests/
```

## 安全边界

这套代码是研究/开发用遥操作网关，不是安全认证控制系统。第一次真机测试请做到：

- UR5e 旁边必须有人手扶急停，操作员视线能看到机械臂和环境。
- 先拆掉末端尖锐工具或重载工具，确认 TCP、payload、重心配置正确。
- 在 UR 示教器上把速度滑块调低，先用很小工作空间测试。
- 保持工作空间内没有人、线缆、夹具干涉和奇异姿态风险。
- 先把 `orientation_control: false` 测通平移，再打开姿态跟随。
- 任何不确定时，释放 Quest 握把键；网页 `Disable` 和手柄 B/Y 也会停止发送运动。

## 安装

需要 Python 3.10 或更高版本。macOS 系统自带的 `/usr/bin/python3` 可能还是 3.9，如果版本不够，可以先安装新版：

```bash
brew install python@3.12
```

```bash
cd /Users/nilueke/Documents/quest——teleop
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp config/teleop.example.yaml config/teleop.yaml
```

真机控制还需要 UR RTDE Python 库。它可能会在本地编译，macOS 上如果缺 `cmake`，先安装：

```bash
brew install cmake
python -m pip install -e ".[real]"
```

如果要采集图像数据，安装相机依赖：

```bash
python -m pip install -e ".[data]"
```

如果要导出 LeRobot 数据集，安装 LeRobot 依赖：

```bash
python -m pip install -e ".[lerobot]"
```

编辑 `config/teleop.yaml`：

```yaml
robot:
  host: 192.168.0.2        # 改成 UR5e 控制柜 IP
  enabled: false           # 干跑保持 false，真机前改 true
  tcp_offset: [0, 0, 0, 0, 0, 0]
  payload_kg:
  payload_cog:

control:
  workspace_min: [0.18, -0.45, 0.08]
  workspace_max: [0.75, 0.45, 0.70]
  max_linear_speed_m_s: 0.08
  max_angular_speed_rad_s: 0.35
  orientation_control: true

recording:
  enabled: true
  root_dir: data/raw
  fps: 20
  default_task: teleoperate the UR5e safely
```

## Quest 访问方式

WebXR 要求安全上下文。推荐开发时用 USB + ADB 反向端口，这样 Quest Browser 打开的 `localhost` 可以直接启用 WebXR。

```bash
adb devices
adb reverse tcp:8080 tcp:8080
```

然后在 Quest Browser 打开：

```text
http://localhost:8080
```

如果要无线访问电脑 IP，需要 HTTPS 且证书被 Quest 信任。可以生成本地证书：

```bash
bash scripts/make_self_signed_cert.sh 你的电脑IP
python -m quest_ur5e_teleop --host 0.0.0.0 --port 8443 \
  --certfile certs/quest-teleop.crt \
  --keyfile certs/quest-teleop.key
```

Quest 上打开 `https://你的电脑IP:8443`。如果浏览器仍提示 WebXR 不可用，优先回到 USB + ADB 方式。

## 干跑验证

先不要接真机：

```bash
source .venv/bin/activate
python -m quest_ur5e_teleop --config config/teleop.yaml --host 0.0.0.0 --port 8080
```

打开页面后：

1. 点 `Enter VR`。
2. 摆好右手柄的中立姿态。
3. 点网页 `Calibrate`，或在 VR 里按 A/X。
4. 点 `Enable Motion`。
5. 按住右手柄握把键移动手柄，页面状态应从 `Live` 变成 `Active`，TCP pose 会变化。
6. 松开握把键，运动停止；按 B/Y 或网页 `Disable` 禁用。

## 数据采集

控制台里有 `Task instruction`、`Start Episode`、`Stop Episode` 和 `Discard`。一次成功演示的推荐流程：

1. 输入自然语言任务，例如 `pick up the red block and place it in the tray`。
2. 点 `Start Episode` 开始采集。
3. 按正常遥操作流程完成任务。
4. 成功就点 `Stop Episode`，失败或中断就点 `Discard`。

数据默认写到 `data/raw/<session_id>/episode_000000/`，包含：

- `metadata.json`：任务、schema、帧数、相机信息。
- `frames.jsonl`：每帧 TCP state、目标 action、Quest 手柄状态。
- `images/<camera>/*.jpg`：启用摄像头后保存的图像。

更完整的 LeRobot / SmolVLA 流程见 [docs/lerobot_smolvla.md](docs/lerobot_smolvla.md)。

## 导出 LeRobot

采集完成后可以导出成 LeRobot dataset：

```bash
quest-ur5e-export-lerobot \
  --raw-root data/raw \
  --repo-id eee336/ur5e_teleop_demo \
  --output-root data/lerobot/ur5e_teleop_demo \
  --force
```

默认导出的 `action` 是绝对 TCP 目标 `[target_tcp_x, target_tcp_y, target_tcp_z, target_tcp_rx, target_tcp_ry, target_tcp_rz, gripper]`。训练 SmolVLA 的模板脚本：

```bash
DATASET_REPO_ID=eee336/ur5e_teleop_demo bash scripts/train_smolvla.sh
```

## 真机运行

UR5e 侧准备：

- 电脑和 UR 控制柜在同一网段，能 `ping` 到 `robot.host`。
- e-Series 控制柜处于 Remote Control，可接受 RTDE 控制连接。
- 没有其他程序占用 RTDE/控制端口。
- 示教器上速度滑块先调低，机器人移动到安全初始姿态。

配置并启动：

```bash
brew install cmake                    # 如果还没装
python -m pip install -e ".[real]"     # 如果还没装 ur-rtde
sed -i.bak 's/enabled: false/enabled: true/' config/teleop.yaml
python -m quest_ur5e_teleop --config config/teleop.yaml --real --host 0.0.0.0 --port 8080
```

真机建议第一次把配置改得更保守：

```yaml
control:
  orientation_control: false
  workspace_min: [0.30, -0.15, 0.20]
  workspace_max: [0.55, 0.15, 0.45]
  max_linear_speed_m_s: 0.03
  max_angular_speed_rad_s: 0.15
```

测试顺序：

1. 页面显示 `RTDE live` 后进入 VR。
2. 手柄放在舒适中立位，标定一次。标定会把“当前 Quest 手柄位姿”绑定到“当前 UR TCP 位姿”。
3. 点击 `Enable Motion`。
4. 只按住握把键做 1 到 2 厘米的小幅移动。
5. 确认方向、比例和限位正确后，再逐步扩大工作空间。

## 坐标映射

Quest WebXR 坐标通常是 `x` 向右、`y` 向上、`-z` 向前。默认配置：

```yaml
position_axes: ["-z", "-x", "y"]
```

含义是：

- 手柄向前移动 -> 机器人基座 `+X`
- 手柄向左移动 -> 机器人基座 `+Y`
- 手柄向上移动 -> 机器人基座 `+Z`

如果你的实验台方向不同，只改 `position_axes`。这个矩阵必须保持右手系，否则程序会拒绝启动，因为姿态映射会变成镜像旋转。

## WebSocket 协议

Quest 页面向 `/ws` 发送：

```json
{
  "type": "pose",
  "handedness": "right",
  "position": [0.1, 1.2, -0.4],
  "orientation": [0, 0, 0, 1],
  "buttons": { "deadman": true, "grip": 1, "trigger": 0 },
  "calibrate": false
}
```

控制消息：

```json
{ "type": "control", "action": "enable" }
{ "type": "control", "action": "disable" }
{ "type": "control", "action": "calibrate" }
{ "type": "control", "action": "reset-calibration" }
{ "type": "control", "action": "start-recording", "task": "pick up the cube" }
{ "type": "control", "action": "stop-recording", "success": true }
{ "type": "control", "action": "discard-recording" }
```

后端会周期性返回：

```json
{ "type": "status", "status": { "active": true, "tcp_pose": [0.4, 0, 0.35, 0, 3.14, 0] } }
```

## 排障

- `WebXR unavailable`：页面不是 `localhost` 或可信 HTTPS；用 `adb reverse` 后打开 `http://localhost:8080`。
- `No Quest controller pose received yet`：先点 `Enter VR`，让右手柄出现在 VR 会话里，再标定。
- 真机不动：确认启动命令带 `--real`，并且 `config/teleop.yaml` 里 `robot.enabled: true`。
- RTDE 连接失败：检查 UR IP、Remote Control、网线、防火墙和是否有其他 RTDE 客户端占用。
- 方向不对：先禁用姿态跟随，只小幅移动，调整 `position_axes`。
- 抖动或跟随太猛：降低 `max_linear_speed_m_s`、`max_angular_speed_rad_s`，或降低 `low_pass_alpha`。
- SmolVLA 训练报缺图像：启用 `recording.cameras` 并重新采集，或仅做 state-only 实验时给导出器加 `--allow-no-images`。
