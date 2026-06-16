import time

import numpy as np

from quest_ur5e_teleop.config import AppConfig
from quest_ur5e_teleop.controller import TeleopController
from quest_ur5e_teleop.robot import SimRobot


def test_controller_clamps_workspace_and_speed():
    config = AppConfig()
    config.control.workspace_min = [0.30, -0.10, 0.20]
    config.control.workspace_max = [0.50, 0.10, 0.45]
    config.control.max_linear_speed_m_s = 0.10
    robot = SimRobot(config)
    robot.connect()
    controller = TeleopController(config, robot, real_robot=False)
    controller.last_commanded_pose = np.asarray(config.robot.initial_tcp_pose, dtype=float)
    controller.update_pose(
        {
            "handedness": "right",
            "position": [0.0, 0.0, 0.0],
            "orientation": [0.0, 0.0, 0.0, 1.0],
            "buttons": {"deadman": True},
        }
    )
    assert controller.calibrate()
    controller.enable()
    controller.update_pose(
        {
            "handedness": "right",
            "position": [10.0, 10.0, 10.0],
            "orientation": [0.0, 0.0, 0.0, 1.0],
            "buttons": {"deadman": True},
        }
    )
    active, target = controller._compute_next_target(time.monotonic())
    assert active
    assert target is not None
    assert target[2] <= config.control.workspace_max[2]
    step = np.linalg.norm(target[:3] - np.asarray(config.robot.initial_tcp_pose[:3]))
    assert step <= config.control.max_linear_speed_m_s / config.control.rate_hz + 1e-9

