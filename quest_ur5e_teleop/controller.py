from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .config import AppConfig
from .recording import EpisodeRecorder, RecordingSample
from .robot import RobotInterface
from .transforms import axis_map_matrix, clamp_vec, matrix_to_rotvec, pose_to_list, quat_to_matrix, rotvec_to_matrix

LOGGER = logging.getLogger(__name__)


@dataclass
class QuestPose:
    position: np.ndarray
    orientation: np.ndarray
    handedness: str
    buttons: dict[str, Any] = field(default_factory=dict)

    @property
    def deadman(self) -> bool:
        return bool(self.buttons.get("deadman", False))


class TeleopController:
    def __init__(self, config: AppConfig, robot: RobotInterface, *, real_robot: bool, recorder: EpisodeRecorder | None = None):
        self.config = config
        self.robot = robot
        self.real_robot = real_robot
        self.recorder = recorder
        self.axis_map = axis_map_matrix(config.control.position_axes)
        self.dt = 1.0 / config.control.rate_hz

        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self.operator_enabled = False
        self.calibrated = False
        self.active = False
        self.stale = True
        self.connected_clients = 0
        self.message_count = 0
        self.last_message_time = 0.0
        self.last_error: str | None = None

        self.latest_pose: QuestPose | None = None
        self.anchor_quest_position: np.ndarray | None = None
        self.anchor_quest_rotation: np.ndarray | None = None
        self.anchor_robot_pose: np.ndarray | None = None
        self.last_commanded_pose: np.ndarray | None = None
        self.filtered_position: np.ndarray | None = None

    def start(self) -> None:
        self.robot.connect()
        self.last_commanded_pose = np.asarray(self.robot.get_tcp_pose(), dtype=float)
        self._thread = threading.Thread(target=self._loop, name="teleop-control-loop", daemon=True)
        self._thread.start()
        LOGGER.info("Teleop control loop started at %s Hz", self.config.control.rate_hz)

    def stop(self) -> None:
        if self.recorder and self.recorder.active:
            try:
                self.recorder.stop(success=False)
            except Exception:
                LOGGER.exception("Failed to stop active recording during shutdown")
        self.disable()
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.robot.stop_motion()
        self.robot.disconnect()

    def client_connected(self) -> None:
        with self._lock:
            self.connected_clients += 1

    def client_disconnected(self) -> None:
        with self._lock:
            self.connected_clients = max(0, self.connected_clients - 1)

    def update_pose(self, message: dict[str, Any]) -> None:
        handedness = str(message.get("handedness", "none"))
        dominant_hand = self.config.control.dominant_hand
        if dominant_hand != "none" and handedness != dominant_hand:
            return

        position = np.asarray(message.get("position", []), dtype=float)
        orientation = np.asarray(message.get("orientation", []), dtype=float)
        if position.shape != (3,) or orientation.shape != (4,):
            raise ValueError("Pose message must contain position[3] and orientation[4]")

        buttons = message.get("buttons") or {}
        with self._lock:
            self.latest_pose = QuestPose(position, orientation, handedness, dict(buttons))
            self.last_message_time = time.monotonic()
            self.message_count += 1

    def enable(self) -> None:
        with self._lock:
            self.operator_enabled = True
            self.last_error = None

    def disable(self) -> None:
        with self._lock:
            self.operator_enabled = False
            self.active = False
        self.robot.stop_motion()

    def calibrate(self) -> bool:
        with self._lock:
            pose = self.latest_pose
            if pose is None:
                self.last_error = "No Quest controller pose received yet"
                return False
            robot_pose = np.asarray(self.robot.get_tcp_pose(), dtype=float)
            self.anchor_quest_position = pose.position.copy()
            self.anchor_quest_rotation = quat_to_matrix(pose.orientation)
            self.anchor_robot_pose = robot_pose.copy()
            self.last_commanded_pose = robot_pose.copy()
            self.filtered_position = robot_pose[:3].copy()
            self.calibrated = True
            self.last_error = None
            LOGGER.info("Calibrated teleop anchor: Quest %s -> robot %s", pose.position, robot_pose)
            return True

    def reset_calibration(self) -> None:
        with self._lock:
            self.calibrated = False
            self.anchor_quest_position = None
            self.anchor_quest_rotation = None
            self.anchor_robot_pose = None
            self.filtered_position = None

    def start_recording(self, task: str | None = None) -> dict[str, Any]:
        if self.recorder is None:
            raise RuntimeError("Recording is not configured")
        return self.recorder.start(
            task=task,
            metadata={
                "real_robot": self.real_robot,
                "control": self.config.control.model_dump(),
                "robot_host": self.config.robot.host if self.real_robot else None,
            },
        )

    def stop_recording(self, *, success: bool = True) -> dict[str, Any]:
        if self.recorder is None:
            raise RuntimeError("Recording is not configured")
        return self.recorder.stop(success=success)

    def discard_recording(self) -> dict[str, Any]:
        if self.recorder is None:
            raise RuntimeError("Recording is not configured")
        return self.recorder.discard()

    def status(self) -> dict[str, Any]:
        with self._lock:
            last_age = time.monotonic() - self.last_message_time if self.last_message_time else None
            pose = self.latest_pose
            tcp = self.last_commanded_pose.tolist() if self.last_commanded_pose is not None else None
            return {
                "real_robot": self.real_robot,
                "operator_enabled": self.operator_enabled,
                "calibrated": self.calibrated,
                "active": self.active,
                "stale": self.stale,
                "deadman": pose.deadman if pose else False,
                "handedness": pose.handedness if pose else None,
                "clients": self.connected_clients,
                "messages": self.message_count,
                "last_message_age_s": last_age,
                "tcp_pose": tcp,
                "last_error": self.last_error,
                "workspace_min": self.config.control.workspace_min,
                "workspace_max": self.config.control.workspace_max,
                "dominant_hand": self.config.control.dominant_hand,
                "recording": self.recorder.status() if self.recorder else None,
            }

    def _loop(self) -> None:
        next_tick = time.monotonic()
        was_active = False
        while not self._stop_event.is_set():
            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(next_tick - now, self.dt))
                continue
            next_tick = now + self.dt

            try:
                active, target = self._compute_next_target(now)
                if active and target is not None:
                    self.robot.servo_l([float(v) for v in target], self.dt)
                    with self._lock:
                        self.last_commanded_pose = target.copy()
                        self.active = True
                    was_active = True
                else:
                    with self._lock:
                        self.active = False
                    if was_active:
                        self.robot.stop_motion()
                        was_active = False
                self._record_if_needed(now)
            except Exception as exc:  # pragma: no cover - defensive stop path
                LOGGER.exception("Control loop error")
                with self._lock:
                    self.operator_enabled = False
                    self.active = False
                    self.last_error = str(exc)
                self.robot.stop_motion()

    def _compute_next_target(self, now: float) -> tuple[bool, np.ndarray | None]:
        with self._lock:
            pose = self.latest_pose
            stale = (now - self.last_message_time) > self.config.control.command_timeout_s if self.last_message_time else True
            self.stale = stale
            can_move = bool(self.operator_enabled and self.calibrated and pose and pose.deadman and not stale)

            if not can_move:
                return False, None

            desired = self._desired_pose_locked(pose)
            safe = self._limit_pose_locked(desired)
            return True, safe

    def _desired_pose_locked(self, pose: QuestPose) -> np.ndarray:
        assert self.anchor_quest_position is not None
        assert self.anchor_quest_rotation is not None
        assert self.anchor_robot_pose is not None

        control = self.config.control
        robot_delta = self.axis_map @ (pose.position - self.anchor_quest_position) * control.position_scale
        target_position = self.anchor_robot_pose[:3] + robot_delta

        if self.filtered_position is None:
            self.filtered_position = target_position.copy()
        else:
            alpha = control.low_pass_alpha
            self.filtered_position = alpha * target_position + (1.0 - alpha) * self.filtered_position
        target_position = self.filtered_position

        anchor_robot_rotation = rotvec_to_matrix(self.anchor_robot_pose[3:6])
        if control.orientation_control:
            quest_rotation = quat_to_matrix(pose.orientation)
            quest_delta = quest_rotation @ self.anchor_quest_rotation.T
            robot_delta_rotation = self.axis_map @ quest_delta @ self.axis_map.T
            target_rotation = robot_delta_rotation @ anchor_robot_rotation
        else:
            target_rotation = anchor_robot_rotation

        return np.asarray(pose_to_list(target_position, target_rotation), dtype=float)

    def _limit_pose_locked(self, desired: np.ndarray) -> np.ndarray:
        control = self.config.control
        previous = self.last_commanded_pose
        if previous is None:
            previous = desired.copy()

        target = desired.copy()
        target[:3] = clamp_vec(target[:3], control.workspace_min, control.workspace_max)

        max_linear_step = control.max_linear_speed_m_s * self.dt
        delta = target[:3] - previous[:3]
        distance = np.linalg.norm(delta)
        if distance > max_linear_step > 0.0:
            target[:3] = previous[:3] + delta / distance * max_linear_step

        previous_rotation = rotvec_to_matrix(previous[3:6])
        target_rotation = rotvec_to_matrix(target[3:6])
        rotation_delta = target_rotation @ previous_rotation.T
        rotation_delta_vec = matrix_to_rotvec(rotation_delta)
        angle = np.linalg.norm(rotation_delta_vec)
        max_angle_step = control.max_angular_speed_rad_s * self.dt
        if angle > max_angle_step > 0.0:
            limited_delta = rotvec_to_matrix(rotation_delta_vec / angle * max_angle_step)
            target_rotation = limited_delta @ previous_rotation
            target[3:6] = matrix_to_rotvec(target_rotation)

        return target

    def _record_if_needed(self, now: float) -> None:
        recorder = self.recorder
        if recorder is None or not recorder.should_sample(now):
            return

        with self._lock:
            target_pose = self.last_commanded_pose.copy() if self.last_commanded_pose is not None else None
            quest_pose = self.latest_pose
            operator_enabled = self.operator_enabled
            calibrated = self.calibrated
            active = self.active
            stale = self.stale

        if target_pose is None:
            return

        try:
            observed_tcp = self.robot.get_tcp_pose()
        except Exception:
            LOGGER.exception("Failed to read robot TCP pose for recording")
            observed_tcp = [float(v) for v in target_pose]

        sample = RecordingSample(
            monotonic_s=now,
            wall_time_s=time.time(),
            observation_tcp_pose=[float(v) for v in observed_tcp],
            target_tcp_pose=[float(v) for v in target_pose],
            quest_position=quest_pose.position.tolist() if quest_pose else None,
            quest_orientation=quest_pose.orientation.tolist() if quest_pose else None,
            handedness=quest_pose.handedness if quest_pose else None,
            buttons=dict(quest_pose.buttons) if quest_pose else {},
            active=active,
            operator_enabled=operator_enabled,
            calibrated=calibrated,
            stale=stale,
        )
        recorder.record(sample)
