import math

import numpy as np

from quest_ur5e_teleop.transforms import axis_map_matrix, matrix_to_rotvec, quat_to_matrix, rotvec_to_matrix


def test_default_axis_map_is_right_handed():
    matrix = axis_map_matrix(["-z", "-x", "y"])
    assert round(float(np.linalg.det(matrix))) == 1


def test_quaternion_to_matrix_identity():
    assert np.allclose(quat_to_matrix([0, 0, 0, 1]), np.eye(3))


def test_rotvec_roundtrip():
    rotvec = np.array([0.1, -0.2, math.pi / 4])
    assert np.allclose(matrix_to_rotvec(rotvec_to_matrix(rotvec)), rotvec)

