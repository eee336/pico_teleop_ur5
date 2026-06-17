from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .config import CameraConfig, RecordingConfig

LOGGER = logging.getLogger(__name__)

STATE_NAMES = ["tcp_x", "tcp_y", "tcp_z", "tcp_rx", "tcp_ry", "tcp_rz", "gripper"]
ACTION_NAMES = ["target_tcp_x", "target_tcp_y", "target_tcp_z", "target_tcp_rx", "target_tcp_ry", "target_tcp_rz", "gripper"]
DELTA_ACTION_NAMES = ["delta_tcp_x", "delta_tcp_y", "delta_tcp_z", "delta_tcp_rx", "delta_tcp_ry", "delta_tcp_rz", "gripper"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _float_list(values: Any, length: int | None = None) -> list[float]:
    arr = np.asarray(values if values is not None else [], dtype=float).reshape(-1)
    if length is not None and arr.size != length:
        raise ValueError(f"Expected {length} values, got {arr.size}")
    return [float(v) for v in arr]


@dataclass
class RecordingSample:
    monotonic_s: float
    wall_time_s: float
    observation_tcp_pose: list[float]
    target_tcp_pose: list[float]
    quest_position: list[float] | None = None
    quest_orientation: list[float] | None = None
    handedness: str | None = None
    buttons: dict[str, Any] = field(default_factory=dict)
    active: bool = False
    operator_enabled: bool = False
    calibrated: bool = False
    stale: bool = True
    gripper: float = 0.0

    def to_frame_payload(self) -> dict[str, Any]:
        observation_state = _float_list([*self.observation_tcp_pose, self.gripper], 7)
        action_absolute_tcp = _float_list([*self.target_tcp_pose, self.gripper], 7)
        tcp_delta = np.asarray(self.target_tcp_pose, dtype=float) - np.asarray(self.observation_tcp_pose, dtype=float)
        action_delta_tcp = _float_list([*tcp_delta, self.gripper], 7)
        return {
            "timestamp_utc": datetime.fromtimestamp(self.wall_time_s, timezone.utc).isoformat(timespec="milliseconds"),
            "time_s": float(self.wall_time_s),
            "observation.state": observation_state,
            "action.absolute_tcp": action_absolute_tcp,
            "action.delta_tcp": action_delta_tcp,
            "robot": {
                "tcp_pose": _float_list(self.observation_tcp_pose, 6),
                "target_tcp_pose": _float_list(self.target_tcp_pose, 6),
            },
            "quest": {
                "position": _float_list(self.quest_position, 3) if self.quest_position is not None else None,
                "orientation": _float_list(self.quest_orientation, 4) if self.quest_orientation is not None else None,
                "handedness": self.handedness,
                "buttons": self.buttons,
            },
            "teleop": {
                "active": bool(self.active),
                "operator_enabled": bool(self.operator_enabled),
                "calibrated": bool(self.calibrated),
                "stale": bool(self.stale),
            },
        }


class CameraCapture:
    def __init__(self, config: CameraConfig, episode_dir: Path, jpeg_quality: int):
        self.config = config
        self.episode_dir = episode_dir
        self.jpeg_quality = jpeg_quality
        self.image_dir = episode_dir / "images" / config.name
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self._cap = None
        self._cv2 = None

    def open(self) -> None:
        try:
            import cv2  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "Camera capture requires OpenCV. Install with `python -m pip install -e '.[data]'`."
            ) from exc

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(self.config.device)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open camera {self.config.name!r} at {self.config.device!r}")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        self._cap.set(cv2.CAP_PROP_FPS, self.config.fps)
        LOGGER.info("Opened camera %s at %s", self.config.name, self.config.device)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def capture(self, frame_index: int) -> dict[str, Any] | None:
        if self._cap is None or self._cv2 is None:
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            LOGGER.warning("Camera %s did not return a frame", self.config.name)
            return None

        rel_path = Path("images") / self.config.name / f"{frame_index:06d}.jpg"
        path = self.episode_dir / rel_path
        self._cv2.imwrite(str(path), frame, [int(self._cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        height, width = frame.shape[:2]
        return {
            "path": rel_path.as_posix(),
            "width": int(width),
            "height": int(height),
            "format": "jpg",
        }


class EpisodeRecorder:
    def __init__(self, config: RecordingConfig, project_root: Path):
        self.config = config
        self.root_dir = (project_root / config.root_dir).resolve() if not Path(config.root_dir).is_absolute() else Path(config.root_dir)
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.session_dir = self.root_dir / self.session_id
        self._lock = threading.RLock()
        self._frame_file = None
        self._current_episode_dir: Path | None = None
        self._current_metadata: dict[str, Any] | None = None
        self._cameras: list[CameraCapture] = []
        self._last_sample_time = 0.0
        self._frame_count = 0
        self._episode_index = 0
        self._last_episode_summary: dict[str, Any] | None = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._current_episode_dir is not None

    def start(self, task: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if not self.config.enabled:
                raise RuntimeError("Recording is disabled in config")
            if self._current_episode_dir is not None:
                raise RuntimeError("An episode is already recording")

            task_text = (task or self.config.default_task).strip() or self.config.default_task
            self.session_dir.mkdir(parents=True, exist_ok=True)
            episode_dir = self.session_dir / f"episode_{self._episode_index:06d}"
            while episode_dir.exists():
                self._episode_index += 1
                episode_dir = self.session_dir / f"episode_{self._episode_index:06d}"
            episode_dir.mkdir(parents=True)

            self._current_episode_dir = episode_dir
            self._frame_file = (episode_dir / "frames.jsonl").open("a", encoding="utf-8")
            self._frame_count = 0
            self._last_sample_time = 0.0
            self._cameras = [CameraCapture(camera, episode_dir, self.config.jpeg_quality) for camera in self.config.cameras if camera.enabled]
            for camera in self._cameras:
                camera.open()

            self._current_metadata = {
                "schema_version": "quest-ur5e-teleop.raw.v1",
                "episode_index": self._episode_index,
                "session_id": self.session_id,
                "task": task_text,
                "started_at": utc_now_iso(),
                "recording_fps": self.config.fps,
                "state_key": "observation.state",
                "action_keys": ["action.absolute_tcp", "action.delta_tcp"],
                "default_export_action_key": "action.absolute_tcp",
                "state_names": STATE_NAMES,
                "action_names": ACTION_NAMES,
                "delta_action_names": DELTA_ACTION_NAMES,
                "cameras": {
                    camera.config.name: {
                        "device": camera.config.device,
                        "width": camera.config.width,
                        "height": camera.config.height,
                        "fps": camera.config.fps,
                    }
                    for camera in self._cameras
                },
                "metadata": metadata or {},
                "frame_count": 0,
                "success": None,
            }
            self._write_metadata_locked()
            LOGGER.info("Started recording episode %s", episode_dir)
            return self.status()

    def stop(self, *, success: bool = True) -> dict[str, Any]:
        with self._lock:
            if self._current_episode_dir is None:
                raise RuntimeError("No episode is recording")
            assert self._current_metadata is not None
            episode_dir = self._current_episode_dir
            self._current_metadata["stopped_at"] = utc_now_iso()
            self._current_metadata["frame_count"] = self._frame_count
            self._current_metadata["success"] = bool(success)
            self._write_metadata_locked()
            self._close_locked()

            summary = {
                "episode_dir": str(episode_dir),
                "session_id": self.session_id,
                "episode_index": self._episode_index,
                "task": self._current_metadata["task"],
                "frame_count": self._frame_count,
                "success": bool(success),
            }
            self._append_manifest(summary)
            self._last_episode_summary = summary
            self._episode_index += 1
            self._current_episode_dir = None
            self._current_metadata = None
            LOGGER.info("Stopped recording episode %s with %s frames", episode_dir, self._frame_count)
            return summary

    def discard(self) -> dict[str, Any]:
        with self._lock:
            if self._current_episode_dir is None:
                raise RuntimeError("No episode is recording")
            episode_dir = self._current_episode_dir
            self._close_locked()
            shutil.rmtree(episode_dir, ignore_errors=True)
            self._current_episode_dir = None
            self._current_metadata = None
            self._frame_count = 0
            summary = {"discarded_episode_dir": str(episode_dir)}
            self._last_episode_summary = summary
            LOGGER.info("Discarded recording episode %s", episode_dir)
            return summary

    def should_sample(self, monotonic_s: float) -> bool:
        with self._lock:
            if self._current_episode_dir is None:
                return False
            if self._last_sample_time == 0.0:
                return True
            return monotonic_s - self._last_sample_time >= (1.0 / self.config.fps)

    def record(self, sample: RecordingSample) -> None:
        with self._lock:
            if self._current_episode_dir is None or self._frame_file is None:
                return
            if self._last_sample_time and sample.monotonic_s - self._last_sample_time < (1.0 / self.config.fps):
                return

            frame_index = self._frame_count
            payload = sample.to_frame_payload()
            payload["frame_index"] = frame_index
            payload["episode_index"] = self._current_metadata["episode_index"] if self._current_metadata else None
            payload["task"] = self._current_metadata["task"] if self._current_metadata else self.config.default_task
            payload["images"] = {}
            for camera in self._cameras:
                image_meta = camera.capture(frame_index)
                if image_meta is not None:
                    payload["images"][camera.config.name] = image_meta

            self._frame_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._frame_file.flush()
            self._frame_count += 1
            self._last_sample_time = sample.monotonic_s

            if self._current_metadata is not None and self._frame_count % max(1, self.config.fps) == 0:
                self._current_metadata["frame_count"] = self._frame_count
                self._write_metadata_locked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.config.enabled,
                "active": self._current_episode_dir is not None,
                "root_dir": str(self.root_dir),
                "session_id": self.session_id,
                "episode_dir": str(self._current_episode_dir) if self._current_episode_dir else None,
                "task": self._current_metadata["task"] if self._current_metadata else None,
                "fps": self.config.fps,
                "frame_count": self._frame_count,
                "camera_count": len([camera for camera in self.config.cameras if camera.enabled]),
                "last_episode": self._last_episode_summary,
            }

    def _write_metadata_locked(self) -> None:
        if self._current_episode_dir is None or self._current_metadata is None:
            return
        metadata_path = self._current_episode_dir / "metadata.json"
        metadata_path.write_text(json.dumps(self._current_metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _append_manifest(self, summary: dict[str, Any]) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self.session_dir / "manifest.jsonl"
        with manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({**summary, "recorded_at": utc_now_iso()}, ensure_ascii=False) + "\n")

    def _close_locked(self) -> None:
        if self._frame_file is not None:
            self._frame_file.close()
            self._frame_file = None
        for camera in self._cameras:
            camera.close()
        self._cameras = []

