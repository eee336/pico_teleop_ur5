from __future__ import annotations

import math
from typing import Iterable

import numpy as np


EPS = 1e-9


def as_vec3(value: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(value), dtype=float)
    if arr.shape != (3,):
        raise ValueError(f"Expected 3 values, got shape {arr.shape}")
    return arr


def normalize_quat(quat_xyzw: Iterable[float]) -> np.ndarray:
    q = np.asarray(list(quat_xyzw), dtype=float)
    if q.shape != (4,):
        raise ValueError(f"Expected quaternion [x, y, z, w], got shape {q.shape}")
    norm = np.linalg.norm(q)
    if norm < EPS:
        raise ValueError("Zero-length quaternion")
    return q / norm


def quat_to_matrix(quat_xyzw: Iterable[float]) -> np.ndarray:
    x, y, z, w = normalize_quat(quat_xyzw)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.array(
        [
            [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
            [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
            [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
        ],
        dtype=float,
    )


def rotvec_to_matrix(rotvec: Iterable[float]) -> np.ndarray:
    rv = as_vec3(rotvec)
    theta = np.linalg.norm(rv)
    if theta < EPS:
        return np.eye(3)
    axis = rv / theta
    x, y, z = axis
    k = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]], dtype=float)
    return np.eye(3) + math.sin(theta) * k + (1.0 - math.cos(theta)) * (k @ k)


def matrix_to_rotvec(matrix: np.ndarray) -> np.ndarray:
    r = np.asarray(matrix, dtype=float)
    if r.shape != (3, 3):
        raise ValueError(f"Expected 3x3 rotation matrix, got {r.shape}")

    trace = float(np.trace(r))
    cos_theta = max(-1.0, min(1.0, (trace - 1.0) / 2.0))
    theta = math.acos(cos_theta)
    if theta < 1e-8:
        return np.zeros(3)

    if abs(math.pi - theta) < 1e-5:
        axis = np.empty(3)
        axis[0] = math.sqrt(max(0.0, (r[0, 0] + 1.0) / 2.0))
        axis[1] = math.sqrt(max(0.0, (r[1, 1] + 1.0) / 2.0))
        axis[2] = math.sqrt(max(0.0, (r[2, 2] + 1.0) / 2.0))
        axis[1] = math.copysign(axis[1], r[0, 1] + r[1, 0])
        axis[2] = math.copysign(axis[2], r[0, 2] + r[2, 0])
        norm = np.linalg.norm(axis)
        if norm < EPS:
            return np.array([theta, 0.0, 0.0])
        return axis / norm * theta

    axis = np.array(
        [r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1]],
        dtype=float,
    )
    axis /= 2.0 * math.sin(theta)
    return axis * theta


def axis_map_matrix(axes: Iterable[str]) -> np.ndarray:
    tokens = list(axes)
    if len(tokens) != 3:
        raise ValueError("position_axes must contain exactly 3 axis entries")

    basis = {"x": 0, "y": 1, "z": 2}
    matrix = np.zeros((3, 3), dtype=float)
    used: set[str] = set()
    for row, token in enumerate(tokens):
        sign = -1.0 if token.startswith("-") else 1.0
        axis = token[1:] if token.startswith("-") else token
        if axis not in basis:
            raise ValueError(f"Invalid axis token {token!r}; use x, y, z, -x, -y or -z")
        if axis in used:
            raise ValueError(f"Axis {axis!r} is used more than once in {tokens!r}")
        used.add(axis)
        matrix[row, basis[axis]] = sign

    det = round(float(np.linalg.det(matrix)))
    if det != 1:
        raise ValueError(
            "position_axes must form a right-handed rotation for orientation control; "
            f"got determinant {np.linalg.det(matrix):.3f}"
        )
    return matrix


def clamp_vec(value: np.ndarray, low: Iterable[float], high: Iterable[float]) -> np.ndarray:
    return np.minimum(np.maximum(value, np.asarray(list(low), dtype=float)), np.asarray(list(high), dtype=float))


def pose_to_list(position: np.ndarray, rotation_matrix: np.ndarray) -> list[float]:
    rotvec = matrix_to_rotvec(rotation_matrix)
    return [float(v) for v in np.concatenate([position, rotvec])]

