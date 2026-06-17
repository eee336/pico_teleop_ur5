# UR5e 数据采集与 SmolVLA 训练

本文档描述这个项目后续用于 LeRobot / SmolVLA 的推荐流程。

参考的官方接口是 LeRobot `main / v0.5.1` 文档。LeRobot v3.0 将低维状态/动作存为 Parquet，将相机数据编码为 MP4，并用 metadata 维护 episode、task、feature schema 和统计信息。SmolVLA 是轻量级机器人基础模型，输入包含多相机视角、机器人 sensorimotor state 和自然语言任务指令。

## 采集内容

每个 raw episode 写入：

```text
data/raw/<session_id>/episode_000000/
  metadata.json
  frames.jsonl
  images/<camera>/000000.jpg      # 启用摄像头时存在
```

每帧的核心字段：

- `observation.state`：7 维，`[tcp_x, tcp_y, tcp_z, tcp_rx, tcp_ry, tcp_rz, gripper]`
- `action.absolute_tcp`：7 维，下一步目标 TCP pose，默认导出为 LeRobot 的 `action`
- `action.delta_tcp`：7 维，`target_tcp_pose - tcp_pose`
- `quest`：Quest 手柄姿态、按钮和 handedness，用于调试或后续研究
- `teleop`：`active`、`operator_enabled`、`calibrated`、`stale`
- `images`：每个相机当前帧的相对路径和尺寸

如果没有夹爪，`gripper` 固定为 `0.0`。后续接 Robotiq 或其他夹爪时，应该把真实开合量写进这一维，并在部署策略时执行同样语义的 action。

## 配置摄像头

SmolVLA 需要视觉观测。建议至少配置一个外部场景相机，任务复杂时加 wrist camera。

```yaml
recording:
  enabled: true
  root_dir: data/raw
  fps: 20
  default_task: pick up the cube and place it in the bin
  cameras:
    - name: front
      enabled: true
      device: 0
      width: 640
      height: 480
      fps: 30
```

安装相机依赖：

```bash
source .venv/bin/activate
python -m pip install -e ".[data]"
```

## 采集 episode

1. 启动网关，先用干跑或极小真机速度确认方向。
2. Quest Browser 打开控制台，输入任务文本。
3. 点 `Start Episode`。
4. 标定、Enable Motion，按住握把键完成一次任务。
5. 点 `Stop Episode` 保存成功 episode；失败或撞到流程中断时点 `Discard`。

SmolVLA 官方文档建议从约 50 条 episode 起步。对于同一个任务里的不同变化，例如物体位置、姿态、光照或目标位置，每个变化都要重复采样，不要只采一次。

## 导出 LeRobot 数据集

安装 LeRobot 依赖：

```bash
source .venv/bin/activate
python -m pip install -e ".[lerobot]"
```

导出本地 LeRobot dataset：

```bash
quest-ur5e-export-lerobot \
  --raw-root data/raw \
  --repo-id eee336/ur5e_teleop_demo \
  --output-root data/lerobot/ur5e_teleop_demo \
  --force
```

同时推到 Hugging Face Hub：

```bash
huggingface-cli login
quest-ur5e-export-lerobot \
  --raw-root data/raw \
  --repo-id eee336/ur5e_teleop_demo \
  --output-root data/lerobot/ur5e_teleop_demo \
  --push-to-hub \
  --force
```

默认把 `action.absolute_tcp` 导出为 LeRobot 的 `action`。如果你想训练相对 TCP 增量：

```bash
quest-ur5e-export-lerobot \
  --raw-root data/raw \
  --repo-id eee336/ur5e_teleop_delta \
  --output-root data/lerobot/ur5e_teleop_delta \
  --action-mode delta_tcp \
  --force
```

## 训练 SmolVLA

安装官方 SmolVLA extra 后运行：

```bash
cd /path/to/lerobot
pip install -e ".[smolvla]"
```

本项目提供了训练脚本模板：

```bash
cd /Users/nilueke/Documents/quest——teleop
DATASET_REPO_ID=eee336/ur5e_teleop_demo \
OUTPUT_DIR=outputs/train/ur5e_smolvla \
JOB_NAME=ur5e_smolvla \
DEVICE=cuda \
bash scripts/train_smolvla.sh
```

脚本默认：

- `--policy.path=lerobot/smolvla_base`
- `--batch_size=64`
- `--steps=20000`
- `--wandb.enable=false`

如果显存不足，先降 `BATCH_SIZE`：

```bash
BATCH_SIZE=8 STEPS=5000 bash scripts/train_smolvla.sh
```

## 数据质量检查

导出或训练前至少检查：

- 每个 episode 的任务文本一致且明确。
- 相机画面能看到机械臂、物体和目标区域，不被手臂长期遮挡。
- `observation.state` 没有 NaN，TCP pose 不跳变。
- 每条失败 episode 都被 `Discard` 或标成 unsuccessful，默认导出器会跳过 unsuccessful episode。
- 同一个任务变化有多次重复，例如 5 个物体位置各 10 条，而不是 50 条都在同一个位置。

## 后续部署约定

训练时的 `action` 语义必须和部署执行一致。本项目默认 action 是绝对 TCP 目标：

```text
[target_tcp_x, target_tcp_y, target_tcp_z, target_tcp_rx, target_tcp_ry, target_tcp_rz, gripper]
```

如果后续希望 SmolVLA 输出 `delta_tcp`，需要同时修改导出参数和策略执行器，让模型输出按相对位移积分到安全限速后的 `servoL` 目标。

