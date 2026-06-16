from __future__ import annotations

import logging
import threading
from typing import Protocol

import numpy as np

from .config import AppConfig

LOGGER = logging.getLogger(__name__)


class RobotInterface(Protocol):
    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def get_tcp_pose(self) -> list[float]: ...

    def servo_l(self, target_pose: list[float], dt: float) -> None: ...

    def stop_motion(self) -> None: ...


class SimRobot:
    def __init__(self, config: AppConfig):
        self._pose = np.asarray(config.robot.initial_tcp_pose, dtype=float)
        self._lock = threading.Lock()
        self.connected = False

    def connect(self) -> None:
        self.connected = True
        LOGGER.info("Sim robot connected")

    def disconnect(self) -> None:
        self.connected = False
        LOGGER.info("Sim robot disconnected")

    def get_tcp_pose(self) -> list[float]:
        with self._lock:
            return [float(v) for v in self._pose]

    def servo_l(self, target_pose: list[float], dt: float) -> None:
        del dt
        with self._lock:
            self._pose = np.asarray(target_pose, dtype=float)

    def stop_motion(self) -> None:
        LOGGER.debug("Sim robot stop requested")


class RTDERobot:
    def __init__(self, config: AppConfig):
        self.config = config
        self.rtde_c = None
        self.rtde_r = None

    def connect(self) -> None:
        try:
            from rtde_control import RTDEControlInterface
            from rtde_receive import RTDEReceiveInterface
        except ImportError as exc:
            raise RuntimeError(
                "Missing ur-rtde Python package. Install with `pip install -r requirements.txt`."
            ) from exc

        host = self.config.robot.host
        LOGGER.info("Connecting to UR RTDE at %s", host)
        self.rtde_c = RTDEControlInterface(host)
        self.rtde_r = RTDEReceiveInterface(host)

        if any(abs(v) > 1e-9 for v in self.config.robot.tcp_offset):
            self.rtde_c.setTcp(self.config.robot.tcp_offset)
            LOGGER.info("Configured TCP offset: %s", self.config.robot.tcp_offset)

        if self.config.robot.payload_kg is not None:
            cog = self.config.robot.payload_cog or [0.0, 0.0, 0.0]
            self.rtde_c.setPayload(self.config.robot.payload_kg, cog)
            LOGGER.info("Configured payload: %.3f kg at %s", self.config.robot.payload_kg, cog)

    def disconnect(self) -> None:
        if self.rtde_c is not None:
            try:
                self.rtde_c.servoStop()
                self.rtde_c.stopScript()
            finally:
                self.rtde_c.disconnect()
        if self.rtde_r is not None:
            self.rtde_r.disconnect()
        LOGGER.info("UR RTDE disconnected")

    def get_tcp_pose(self) -> list[float]:
        if self.rtde_r is None:
            raise RuntimeError("UR RTDE receive interface is not connected")
        return [float(v) for v in self.rtde_r.getActualTCPPose()]

    def servo_l(self, target_pose: list[float], dt: float) -> None:
        if self.rtde_c is None:
            raise RuntimeError("UR RTDE control interface is not connected")
        control = self.config.control
        self.rtde_c.servoL(
            target_pose,
            control.servo_speed_m_s,
            control.servo_accel_m_s2,
            dt,
            control.servo_lookahead_s,
            control.servo_gain,
        )

    def stop_motion(self) -> None:
        if self.rtde_c is not None:
            self.rtde_c.servoStop()

