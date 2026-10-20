"""Camera pose per segment, approximate intrinsics, plane agreement."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .detect import SegmentDetection
from . import targets

MIN_CORNERS = 6


@dataclass
class Pose:
    """Segment frame -> camera frame: X_cam = R @ X_seg + t (inches)."""

    R: np.ndarray
    t: np.ndarray
    rms_px: float
    n: int
    centroid: np.ndarray  # mean of the used object points, segment frame

    @property
    def rvec(self) -> np.ndarray:
        return cv2.Rodrigues(self.R)[0]

    @property
    def normal_cam(self) -> np.ndarray:
        """Segment +z (into the board) in camera coordinates."""
        return self.R[:, 2]

    @property
    def distance_in(self) -> float:
        return float(np.linalg.norm(self.R @ self.centroid + self.t))

    @property
    def angle_deg(self) -> float:
        """Angle between the line of sight to the centroid and the board normal."""
        ray = self.R @ self.centroid + self.t
        ray = ray / np.linalg.norm(ray)
        return float(np.degrees(np.arccos(np.clip(abs(ray @ self.normal_cam), -1, 1))))

    @property
    def camera_center(self) -> np.ndarray:
        """Camera position in the segment frame."""
        return -self.R.T @ self.t


def approx_intrinsics(width: int, height: int, focal35: float | None = None,
                      focal_mm: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Rough camera matrix from EXIF when there is no calibration."""
    diag = float(np.hypot(width, height))
    if focal35:
        f = focal35 / 43.27 * diag
    else:
        f = 0.85 * max(width, height)  # typical phone main camera (~26 mm equiv.)
    K = np.array([[f, 0, (width - 1) / 2], [0, f, (height - 1) / 2], [0, 0, 1]], dtype=np.float64)
    return K, np.zeros(5)


def solve(obj: np.ndarray, img: np.ndarray, K: np.ndarray, dist: np.ndarray) -> Pose | None:
    obj = np.asarray(obj, np.float64).reshape(-1, 3)
    img = np.asarray(img, np.float64).reshape(-1, 2)
    if len(obj) < MIN_CORNERS:
        return None
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        return None
    rvec, tvec = cv2.solvePnPRefineLM(obj, img, K, dist, rvec, tvec)
    proj, _ = cv2.projectPoints(obj, rvec, tvec, K, dist)
    rms = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img) ** 2, axis=1))))
    R = cv2.Rodrigues(rvec)[0]
    return Pose(R, tvec.reshape(3), rms, len(obj), obj.mean(axis=0))


def segment_pose(sd: SegmentDetection, K, dist, scale=(1.0, 1.0)) -> Pose | None:
    if sd.n_corners < MIN_CORNERS:
        return None
    obj = targets.object_points(sd.segment, scale)[sd.corner_ids]
    return solve(obj, sd.corners, K, dist)


def undistort_normalized(uv: np.ndarray, K: np.ndarray, dist: np.ndarray, iters: int = 20) -> np.ndarray:
    """Pixel coordinates (N, 2) -> ideal normalized coordinates (N, 2).

    Fixed-point inversion of OpenCV's 5-term model (k1, k2, p1, p2, k3).
    """
    uv = np.asarray(uv, np.float64).reshape(-1, 2)
    d = np.zeros(5)
    dd = np.asarray(dist, np.float64).ravel()[:5]
    d[:len(dd)] = dd
    k1, k2, p1, p2, k3 = d
    y0 = (uv[:, 1] - K[1, 2]) / K[1, 1]
    x0 = (uv[:, 0] - K[0, 2] - K[0, 1] * y0) / K[0, 0]
    x, y = x0.copy(), y0.copy()
    if not d.any():
        return np.column_stack([x, y])
    for _ in range(iters):
        r2 = x * x + y * y
        radial = 1 + r2 * (k1 + r2 * (k2 + r2 * k3))
        dx = 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
        dy = p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
        x = (x0 - dx) / radial
        y = (y0 - dy) / radial
    return np.column_stack([x, y])


def plane_agreement(a: Pose, b: Pose) -> dict:
    """How well segment b lies in segment a's plane."""
    ang = float(np.degrees(np.arccos(np.clip(abs(a.normal_cam @ b.normal_cam), -1, 1))))
    cb = b.R @ b.centroid + b.t  # b's centroid in camera frame
    in_a = a.R.T @ (cb - a.t)  # ... in a's segment frame
    return {"angle_deg": ang, "offset_in": float(in_a[2])}
