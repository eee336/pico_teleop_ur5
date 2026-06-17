from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .transforms import axis_map_matrix


class ServerConfig(BaseModel):
    static_dir: str = "web"


class RobotConfig(BaseModel):
    host: str = "192.168.0.2"
    enabled: bool = False
    initial_tcp_pose: list[float] = Field(default_factory=lambda: [0.40, 0.00, 0.35, 0.0, 3.14159, 0.0])
    tcp_offset: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    payload_kg: float | None = None
    payload_cog: list[float] | None = None

    @field_validator("initial_tcp_pose", "tcp_offset")
    @classmethod
    def _pose_has_six_values(cls, value: list[float]) -> list[float]:
        if len(value) != 6:
            raise ValueError("Pose values must contain [x, y, z, rx, ry, rz]")
        return value

    @field_validator("payload_cog")
    @classmethod
    def _payload_cog_has_three_values(cls, value: list[float] | None) -> list[float] | None:
        if value is not None and len(value) != 3:
            raise ValueError("payload_cog must contain [x, y, z]")
        return value


class ControlConfig(BaseModel):
    rate_hz: int = 125
    dominant_hand: str = "right"
    position_scale: float = 1.0
    position_axes: list[str] = Field(default_factory=lambda: ["-z", "-x", "y"])
    orientation_control: bool = True
    workspace_min: list[float] = Field(default_factory=lambda: [0.18, -0.45, 0.08])
    workspace_max: list[float] = Field(default_factory=lambda: [0.75, 0.45, 0.70])
    max_linear_speed_m_s: float = 0.08
    max_angular_speed_rad_s: float = 0.35
    command_timeout_s: float = 0.25
    low_pass_alpha: float = 0.35
    servo_speed_m_s: float = 0.20
    servo_accel_m_s2: float = 0.50
    servo_lookahead_s: float = 0.10
    servo_gain: int = 300

    @field_validator("dominant_hand")
    @classmethod
    def _valid_hand(cls, value: str) -> str:
        if value not in {"left", "right", "none"}:
            raise ValueError("dominant_hand must be left, right or none")
        return value

    @field_validator("workspace_min", "workspace_max")
    @classmethod
    def _vec3(cls, value: list[float]) -> list[float]:
        if len(value) != 3:
            raise ValueError("Workspace limits must contain [x, y, z]")
        return value

    @model_validator(mode="after")
    def _valid_control_values(self) -> "ControlConfig":
        axis_map_matrix(self.position_axes)
        if any(a >= b for a, b in zip(self.workspace_min, self.workspace_max)):
            raise ValueError("Each workspace_min value must be smaller than workspace_max")
        if self.rate_hz < 20 or self.rate_hz > 500:
            raise ValueError("rate_hz should be between 20 and 500")
        if not (0.01 <= self.position_scale <= 5.0):
            raise ValueError("position_scale should be between 0.01 and 5.0")
        if not (0.0 < self.low_pass_alpha <= 1.0):
            raise ValueError("low_pass_alpha must be in (0, 1]")
        return self


class CameraConfig(BaseModel):
    name: str = "front"
    enabled: bool = False
    device: int | str = 0
    width: int = 640
    height: int = 480
    fps: int = 30

    @model_validator(mode="after")
    def _valid_camera(self) -> "CameraConfig":
        if not self.name.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Camera names must contain only letters, numbers, hyphens and underscores")
        if self.width <= 0 or self.height <= 0 or self.fps <= 0:
            raise ValueError("Camera width, height and fps must be positive")
        return self


class RecordingConfig(BaseModel):
    enabled: bool = True
    root_dir: str = "data/raw"
    fps: int = 20
    default_task: str = "teleoperate the UR5e safely"
    jpeg_quality: int = 90
    cameras: list[CameraConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid_recording(self) -> "RecordingConfig":
        if self.fps <= 0 or self.fps > 120:
            raise ValueError("recording.fps must be in [1, 120]")
        if self.jpeg_quality < 1 or self.jpeg_quality > 100:
            raise ValueError("recording.jpeg_quality must be in [1, 100]")
        names = [camera.name for camera in self.cameras if camera.enabled]
        if len(names) != len(set(names)):
            raise ValueError("Enabled camera names must be unique")
        return self


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    robot: RobotConfig = Field(default_factory=RobotConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)


def _deep_update(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_update(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path | None = None) -> AppConfig:
    data: dict[str, Any] = {}
    if path:
        config_path = Path(path)
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle) or {}
                if not isinstance(loaded, dict):
                    raise ValueError(f"{config_path} must contain a YAML mapping")
                data = loaded
        else:
            raise FileNotFoundError(config_path)

    defaults = AppConfig().model_dump()
    return AppConfig.model_validate(_deep_update(defaults, data))
