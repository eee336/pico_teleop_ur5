# Quest / UR5e / LeRobot / SmolVLA 端到端流程

本文档描述从 UR5e 遥操作采集，到 LeRobot 数据集导出，到 SmolVLA 训练，再到验证和真机评估的完整流程。

当前项目已经实现：

- Meta Quest WebXR 遥操作 UR5e。
- raw episode 数据采集。
- raw episode 导出为 LeRobot dataset。
- SmolVLA 训练脚本模板。

当前项目尚未实现：

- 训练好的 SmolVLA policy 自动接回 UR5e 执行。

也就是说，现在的闭环是“采集 -> 导出 -> 训练 -> 离线验证”。真机自动验证需要后续新增 policy runner。

## 0. 总体数据流

```text
Quest 手柄 + 相机 + UR5e
        |
        v
Python teleop gateway
        |
        v
data/raw/<session_id>/episode_xxxxxx/
        |
        v
quest-ur5e-export-lerobot
        |
        v
LeRobot dataset
        |
        v
SmolVLA fine-tuning
        |
        v
离线验证 / 后续 policy runner 真机验证
```

## 1. 环境准备

进入项目：

```bash
cd /Users/nilueke/Documents/quest——teleop
source .venv/bin/activate
```

基础依赖：

```bash
python -m pip install -e ".[dev]"
```

真机 UR5e 控制依赖：

```bash
brew install cmake
python -m pip install -e ".[real]"
```

相机采集依赖：

```bash
python -m pip install -e ".[data]"
```

LeRobot 导出依赖：

```bash
python -m pip install -e ".[lerobot]"
```

如果要训练 SmolVLA，通常需要在 LeRobot 官方仓库环境里安装对应 extra：

```bash
cd /path/to/lerobot
pip install -e ".[smolvla]"
```

## 2. 配置机器人和采集参数

复制配置：

```bash
cp config/teleop.example.yaml config/teleop.yaml
```

编辑 `config/teleop.yaml`。

机器人部分：

```yaml
robot:
  host: 192.168.0.2
  enabled: true
  tcp_offset: [0, 0, 0, 0, 0, 0]
  payload_kg:
  payload_cog:
```

安全控制部分：

```yaml
control:
  orientation_control: false
  workspace_min: [0.30, -0.15, 0.20]
  workspace_max: [0.55, 0.15, 0.45]
  max_linear_speed_m_s: 0.03
  max_angular_speed_rad_s: 0.15
```

第一次真机采集建议先关闭姿态跟随，也就是 `orientation_control: false`，确认平移方向和工作空间之后再打开。

采集部分：

```yaml
recording:
  enabled: true
  root_dir: data/raw
  fps: 20
  default_task: pick up the cube and place it in the tray
  cameras:
    - name: front
      enabled: true
      device: 0
      width: 640
      height: 480
      fps: 30
```

SmolVLA 需要视觉输入。至少建议启用一个外部场景相机，让画面同时看到机械臂、物体和目标区域。

## 3. 启动遥操作网关

干跑模式：

```bash
python -m quest_ur5e_teleop \
  --config config/teleop.yaml \
  --host 0.0.0.0 \
  --port 8080
```

真机模式：

```bash
python -m quest_ur5e_teleop \
  --config config/teleop.yaml \
  --real \
  --host 0.0.0.0 \
  --port 8080
```

Quest 通过 USB ADB 访问：

```bash
adb reverse tcp:8080 tcp:8080
```

然后在 Quest Browser 打开：

```text
http://localhost:8080
```

## 4. 单条 episode 采集流程

在 Quest 控制台页面执行：

1. 点击 `Enter VR`。
2. 在 `Task instruction` 输入任务文本。
3. 点击 `Start Episode`。
4. 将右手柄放在舒适中立位。
5. 点击 `Calibrate`，或在手柄上按 A/X。
6. 点击 `Enable Motion`。
7. 按住右手柄握把键作为 deadman。
8. 遥操作 UR5e 完成任务。
9. 成功完成就点击 `Stop Episode`。
10. 失败、碰撞风险、流程不自然或被打断就点击 `Discard`。

一次 episode 的输出目录类似：

```text
data/raw/20260617T024706Z/episode_000000/
  metadata.json
  frames.jsonl
  images/front/000000.jpg
  images/front/000001.jpg
```

`metadata.json` 记录任务文本、采集帧率、相机信息、frame 数量和成功标记。

`frames.jsonl` 每行是一帧，关键字段包括：

```text
observation.state
action.absolute_tcp
action.delta_tcp
robot.tcp_pose
robot.target_tcp_pose
quest.position
quest.orientation
quest.buttons
teleop.active
images.<camera>.path
```

默认 state/action 语义：

```text
observation.state:
[tcp_x, tcp_y, tcp_z, tcp_rx, tcp_ry, tcp_rz, gripper]

action.absolute_tcp:
[target_tcp_x, target_tcp_y, target_tcp_z, target_tcp_rx, target_tcp_ry, target_tcp_rz, gripper]

action.delta_tcp:
[delta_tcp_x, delta_tcp_y, delta_tcp_z, delta_tcp_rx, delta_tcp_ry, delta_tcp_rz, gripper]
```

当前没有真实夹爪接入时，`gripper` 固定为 `0.0`。

## 5. 采集数量建议

SmolVLA 起步建议至少约 50 条成功 episode。

不要只在同一个场景重复 50 次。更好的分布是：

```text
5 个物体初始位置 x 每个位置 10 条 = 50 条
```

或者：

```text
5 个物体位置 x 2 个目标位置 x 每组 5 条 = 50 条
```

每条 episode 都应该有明确任务文本。如果任务文本变化，后续训练和评估也要按 task 区分。

## 6. 数据质量检查

采完一批数据后先检查 raw 数据，不要马上训练。

检查点：

- `metadata.json` 里的 `success` 是否为 `true`。
- `frame_count` 是否符合 episode 时长。
- `frames.jsonl` 是否没有空文件。
- 相机图像是否清晰，且看到物体、目标区域和机械臂。
- 失败 episode 是否已经 `Discard`。
- 任务文本是否准确，例如不要把不同任务混成同一句。
- TCP pose 是否没有突然跳到工作空间外。
- 采集过程是否没有长时间卡顿、遮挡或手柄丢追踪。

可以快速查看 raw episode：

```bash
find data/raw -name metadata.json -print
find data/raw -name frames.jsonl -print
```

## 7. 导出 LeRobot 数据集

本地导出：

```bash
quest-ur5e-export-lerobot \
  --raw-root data/raw \
  --repo-id eee336/ur5e_teleop_demo \
  --output-root data/lerobot/ur5e_teleop_demo \
  --force
```

导出并上传 Hugging Face Hub：

```bash
huggingface-cli login

quest-ur5e-export-lerobot \
  --raw-root data/raw \
  --repo-id eee336/ur5e_teleop_demo \
  --output-root data/lerobot/ur5e_teleop_demo \
  --push-to-hub \
  --force
```

默认导出 `action.absolute_tcp` 为 LeRobot 的 `action`。

如果希望训练相对动作：

```bash
quest-ur5e-export-lerobot \
  --raw-root data/raw \
  --repo-id eee336/ur5e_teleop_delta \
  --output-root data/lerobot/ur5e_teleop_delta \
  --action-mode delta_tcp \
  --force
```

注意：训练时选择 absolute 或 delta，部署执行时也必须使用同样语义。

## 8. LeRobot 数据集检查

导出后应该确认：

- dataset 能被 LeRobot 正常加载。
- `observation.state` shape 是 7。
- `action` shape 是 7。
- 图像 key 类似 `observation.images.front`。
- task 文本能正确读取。
- episode 数量和 raw 成功 episode 数量一致。

如果导出时报没有图像，而你只是想做 state-only 实验，可以加：

```bash
--allow-no-images
```

但 SmolVLA 正常训练建议使用图像。

## 9. 训练 SmolVLA

使用项目提供的脚本：

```bash
DATASET_REPO_ID=eee336/ur5e_teleop_demo \
OUTPUT_DIR=outputs/train/ur5e_smolvla \
JOB_NAME=ur5e_smolvla \
DEVICE=cuda \
bash scripts/train_smolvla.sh
```

显存不足时降低 batch size：

```bash
DATASET_REPO_ID=eee336/ur5e_teleop_demo \
BATCH_SIZE=8 \
STEPS=5000 \
bash scripts/train_smolvla.sh
```

脚本默认使用：

```text
policy.path = lerobot/smolvla_base
batch_size = 64
steps = 20000
wandb.enable = false
```

训练输出默认在：

```text
outputs/train/ur5e_smolvla
```

## 10. 训练过程观察

训练时关注：

- loss 是否整体下降。
- loss 是否很快过拟合到很低，但验证或 rollout 失败。
- action 输出是否经常超过机械臂安全工作空间。
- 图像增强或相机视角是否导致物体不可见。
- 不同 task 或不同物体位置是否表现差异很大。

如果训练不稳定，优先检查数据，而不是先调模型：

- episode 数量是否太少。
- 成功/失败数据是否混在一起。
- 图像和 action 是否错位。
- 相机帧率和 recording fps 是否差太多。
- 任务描述是否不一致。

## 11. 离线验证

离线验证目标是确认模型输出“形状正确、数值合理、随观察变化”。

建议做三类检查：

1. 加载 checkpoint，确认能对一帧 observation 推理出 7 维 action。
2. 在验证 episode 上逐帧推理，比较模型 action 和数据集 action。
3. 对输出 action 做安全检查，例如工作空间 clamp 前后差异是否过大。

判断标准：

- action 没有 NaN。
- TCP 目标不频繁跳变。
- 输出在训练数据覆盖的空间附近。
- 同一 episode 上动作方向大致跟示教一致。

## 12. 真机验证

当前项目还没有 policy runner，因此真机自动验证需要后续新增执行器。

新增 policy runner 时应该复用现有遥操作安全逻辑：

- 工作空间限制。
- 线速度和角速度限制。
- command timeout。
- 急停和人工 deadman。
- `servoL` 控制参数。

真机验证建议分阶段：

1. 只加载模型，不连接 UR5e，打印 action。
2. 连接相机和模型，不连接 UR5e，检查实时推理频率。
3. 连接 UR5e，但只做 dry-run，不发 `servoL`。
4. 发 `servoL` 前对 action 做 workspace clamp 和速度限制。
5. 示教器速度滑块调低，只测 1 个简单任务。
6. 每次只扩大一个变量，例如物体位置或目标位置。

任何阶段出现异常，回到上一阶段排查。

## 13. 推荐里程碑

第一阶段：遥操作稳定

- 干跑数据流正常。
- 真机低速遥操作稳定。
- Quest 手柄方向正确。
- 工作空间限制正确。

第二阶段：raw 数据稳定

- 能连续采 10 条 episode。
- `Stop Episode` 和 `Discard` 行为可靠。
- 图像和 state/action 对齐。

第三阶段：LeRobot 导出稳定

- 能导出本地 dataset。
- 能加载 dataset。
- 能 push 到 Hugging Face Hub。

第四阶段：SmolVLA 训练稳定

- loss 正常下降。
- checkpoint 能推理。
- 离线 action 合理。

第五阶段：policy runner

- 模型实时读取相机和机器人 state。
- 模型输出经过安全限幅。
- 能从 dry-run 过渡到小范围真机执行。

## 14. 常见问题

`WebXR unavailable`

使用 `adb reverse tcp:8080 tcp:8080`，然后在 Quest Browser 打开 `http://localhost:8080`。

`No Quest controller pose received yet`

先进入 VR，让右手柄被 WebXR 追踪，再标定。

真机不动

确认启动命令有 `--real`，并且 `config/teleop.yaml` 里 `robot.enabled: true`。

导出时报没有图像

SmolVLA 训练需要视觉输入。启用 `recording.cameras` 后重新采集。仅做 state-only 实验时可以加 `--allow-no-images`。

模型训练后动作很乱

优先检查数据质量：图像是否清晰、任务是否混杂、失败 episode 是否混入、动作是否跳变、采集数量是否太少。

部署时动作方向反了

检查训练 action 是 `absolute_tcp` 还是 `delta_tcp`。训练和部署必须一致。

