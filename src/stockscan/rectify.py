"""Straight-on resampling of the face plane.

The face plane is the plane of a front strip segment, shifted toward the
camera by a configured offset (positive when the stock ends stand proud
of the strip).  The rectified image has a fixed scale: pixel (c, r) is the
plane point (x0 + (c + 0.5) / ppi, y0 + (r + 0.5) / ppi) in segment inches.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .pose import Pose, undistort_normalized

MAX_SIDE_PX = 8000


@dataclass
class FacePlane:
    pose: Pose
    K: np.ndarray
    dist: np.ndarray
    offset_in: float = 0.0

    @property
    def z(self) -> float:
        return -self.offset_in

    def to_image(self, xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, np.float64).reshape(-1, 2)
        obj = np.column_stack([xy, np.full(len(xy), self.z)])
        uv, _ = cv2.projectPoints(obj, self.pose.rvec, self.pose.t, self.K, self.dist)
        return uv.reshape(-1, 2)

    def to_plane(self, uv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Image pixels -> plane (x, y); second value is False where the ray misses."""
        n = undistort_normalized(uv, self.K, self.dist)
        d = np.column_stack([n, np.ones(len(n))]) @ self.pose.R  # R^T d_cam, as rows
        C = self.pose.camera_center
        with np.errstate(divide="ignore", invalid="ignore"):
            s = (self.z - C[2]) / d[:, 2]
        xy = C[None, :2] + s[:, None] * d[:, :2]
        return xy, s > 0


@dataclass
class Rectified:
    image: np.ndarray
    x0: float
    y0: float
    ppi: float

    def to_plane(self, cr: np.ndarray) -> np.ndarray:
        cr = np.asarray(cr, np.float64).reshape(-1, 2)
        return np.column_stack([self.x0 + (cr[:, 0] + 0.5) / self.ppi, self.y0 + (cr[:, 1] + 0.5) / self.ppi])

    def to_px(self, xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, np.float64).reshape(-1, 2)
        return np.column_stack([(xy[:, 0] - self.x0) * self.ppi - 0.5, (xy[:, 1] - self.y0) * self.ppi - 0.5])

    def meta(self) -> dict:
        return {"x0": self.x0, "y0": self.y0, "ppi": self.ppi,
                "width": int(self.image.shape[1]), "height": int(self.image.shape[0])}


def visible_x_range(plane: FacePlane, image_size: tuple[int, int], y_range: tuple[float, float],
                    limit: tuple[float, float]) -> tuple[float, float] | None:
    """x extent of the plane band y_range that falls inside the image."""
    w, h = image_size
    gu, gv = np.meshgrid(np.linspace(0, w - 1, 41), np.linspace(0, h - 1, 31))
    xy, ok = plane.to_plane(np.column_stack([gu.ravel(), gv.ravel()]))
    sel = ok & (xy[:, 1] >= y_range[0]) & (xy[:, 1] <= y_range[1])
    if not sel.any():
        return None
    x0 = max(float(xy[sel, 0].min()), limit[0])
    x1 = min(float(xy[sel, 0].max()), limit[1])
    return (x0, x1) if x1 > x0 else None


def rectify(image: np.ndarray, plane: FacePlane, x_range: tuple[float, float],
            y_range: tuple[float, float], ppi: float = 100.0) -> Rectified:
    x0, x1 = x_range
    y0, y1 = y_range
    W = int(np.ceil((x1 - x0) * ppi))
    H = int(np.ceil((y1 - y0) * ppi))
    if W > MAX_SIDE_PX or H > MAX_SIDE_PX:
        raise ValueError(f"rectified image too large ({W}x{H} px); reduce the area or the scale")
    if W < 2 or H < 2:
        raise ValueError("rectified area is empty")
    # project a coarse grid of plane points, then interpolate the maps
    step = 8
    nx, ny = W // step + 2, H // step + 2
    gc, gr = np.meshgrid(np.arange(nx) * step, np.arange(ny) * step)
    xy = np.column_stack([x0 + (gc.ravel() + 0.5) / ppi, y0 + (gr.ravel() + 0.5) / ppi])
    uv = plane.to_image(xy).astype(np.float32)
    coarse = uv.reshape(ny, nx, 2)
    fx, fy = np.meshgrid(np.arange(W, dtype=np.float32) / step, np.arange(H, dtype=np.float32) / step)
    fine = cv2.remap(coarse, fx, fy, cv2.INTER_LINEAR)
    out = cv2.remap(image, fine[..., 0], fine[..., 1], cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return Rectified(out, x0, y0, ppi)


def face_band(strip_height_in: float, above_in: float, below_in: float = 0.5) -> tuple[float, float]:
    """y range of the rectified area: above_in above the strip to below_in under it."""
    return (-above_in, strip_height_in + below_in)
