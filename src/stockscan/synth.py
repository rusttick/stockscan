"""Synthetic scenes with known ground truth, for tests and the self-test.

A scene is a flat textured plane (inches, x right, y down, z into the plane)
viewed by a virtual pinhole camera with lens distortion.  Pixels are
rendered by tracing each (supersampled) image pixel back to the plane.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from . import targets
from .pose import undistort_normalized
from .targets import Segment

SIZE = (2000, 1500)
K_TRUE = np.array([[1800.0, 0, 1003.0], [0, 1795.0, 748.0], [0, 0, 1]])
DIST_TRUE = np.array([-0.11, 0.06, 0.0004, -0.0003, 0.0])


def look_at(cam_pos, target, roll_deg: float = 0.0):
    """World (plane) -> camera rotation and translation for a camera at cam_pos
    looking at target, with image 'down' close to world +y."""
    c = np.asarray(cam_pos, float)
    z = np.asarray(target, float) - c
    z /= np.linalg.norm(z)
    down = np.array([0.0, 1.0, 0.0])
    y = down - (down @ z) * z
    y /= np.linalg.norm(y)
    x = np.cross(y, z)
    R = np.vstack([x, y, z])
    if roll_deg:
        a = np.radians(roll_deg)
        Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
        R = Rz @ R
    return R, -R @ c


@dataclass
class Canvas:
    """Texture of the plane: x0, y0 = plane coordinates of pixel (0, 0)."""

    x0: float
    y0: float
    width_in: float
    height_in: float
    ppi: float = 200.0
    background: int = 60
    img: np.ndarray = field(init=False)

    def __post_init__(self):
        self.img = np.full((int(round(self.height_in * self.ppi)), int(round(self.width_in * self.ppi))),
                           self.background, np.uint8)

    def _px(self, x, y):
        return int(round((x - self.x0) * self.ppi)), int(round((y - self.y0) * self.ppi))

    def rect(self, x, y, w, h, value):
        c0, r0 = self._px(x, y)
        c1, r1 = self._px(x + w, y + h)
        self.img[max(r0, 0):max(r1, 0), max(c0, 0):max(c1, 0)] = value

    def paste(self, raster: np.ndarray, x, y):
        """Paste a raster rendered at this canvas's ppi with its top-left at (x, y)."""
        c0, r0 = self._px(x, y)
        h, w = raster.shape
        rs, cs = max(0, -r0), max(0, -c0)
        re_, ce = min(h, self.img.shape[0] - r0), min(w, self.img.shape[1] - c0)
        if re_ > rs and ce > cs:
            self.img[r0 + rs:r0 + re_, c0 + cs:c0 + ce] = raster[rs:re_, cs:ce]

    def paste_segment(self, page: targets.Page, seg: Segment, x, y, pad: float = 0.25):
        """Paste the part of a page around a segment so the segment origin lands at (x, y)."""
        r = targets.rasterize(page, self.ppi)
        c0 = int(round((seg.x_in - pad) * self.ppi))
        r0 = int(round((seg.y_in - pad) * self.ppi))
        c1 = int(round((seg.x_in + seg.width_in + pad) * self.ppi))
        r1 = int(round((seg.y_in + seg.height_in + pad) * self.ppi))
        self.paste(r[max(r0, 0):r1, max(c0, 0):c1], x - pad, y - pad)

    def paste_page(self, page: targets.Page, seg: Segment, x, y):
        """Paste a whole page so that the segment origin lands at (x, y)."""
        self.paste(targets.rasterize(page, self.ppi), x - seg.x_in, y - seg.y_in)


def render(canvas: Canvas, K, dist, R, t, size=SIZE, supersample: int = 2, step: int = 8) -> np.ndarray:
    W, H = size
    ss = supersample
    # Trace rays on a coarse grid of supersampled pixels; the mapping is
    # smooth, so bilinear interpolation between grid points is exact enough.
    nx, ny = W * ss // step + 2, H * ss // step + 2
    gu, gv = np.meshgrid(np.arange(nx) * step, np.arange(ny) * step)
    u = (gu.ravel() + 0.5) / ss - 0.5
    v = (gv.ravel() + 0.5) / ss - 0.5
    norm = undistort_normalized(np.column_stack([u, v]), K, dist)
    d = np.column_stack([norm, np.ones(len(norm))]) @ R  # rows: R^T @ d_cam
    C = -R.T @ t
    with np.errstate(divide="ignore", invalid="ignore"):
        s = -C[2] / d[:, 2]
    P = C[None, :2] + s[:, None] * d[:, :2]
    mx = ((P[:, 0] - canvas.x0) * canvas.ppi - 0.5).astype(np.float32)
    my = ((P[:, 1] - canvas.y0) * canvas.ppi - 0.5).astype(np.float32)
    bad = ~(s > 0)
    mx[bad] = -1e6
    my[bad] = -1e6
    coarse = np.dstack([mx.reshape(ny, nx), my.reshape(ny, nx)])
    fx, fy = np.meshgrid(np.arange(W * ss, dtype=np.float32) / step, np.arange(H * ss, dtype=np.float32) / step)
    fine = cv2.remap(coarse, fx, fy, cv2.INTER_LINEAR)
    out = cv2.remap(canvas.img, fine[..., 0], fine[..., 1],
                    cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=30)
    if ss > 1:
        out = cv2.resize(out, (W, H), interpolation=cv2.INTER_AREA)
    return out


def degrade(img: np.ndarray, rng: np.random.Generator, blur: float = 0.7, noise: float = 2.0) -> np.ndarray:
    out = img.astype(np.float32)
    if blur > 0:
        out = cv2.GaussianBlur(out, (0, 0), blur)
    if noise > 0:
        out += rng.normal(0, noise, out.shape).astype(np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)


def calibration_views(rec: dict, n: int = 18, seed: int = 1, K=K_TRUE, dist=DIST_TRUE, size=SIZE):
    """Views of a calibration board print from varied positions."""
    rng = np.random.default_rng(seed)
    page = targets.pages_for_print(rec)[0]
    seg = Segment.from_dict(rec["segments"][0])
    canvas = Canvas(-seg.x_in - 2, -seg.y_in - 2, page.width + 4, page.height + 4, ppi=100)
    canvas.paste_page(page, seg, 0, 0)
    w, h = seg.width_in, seg.height_in
    images = []
    for i in range(n):
        target = np.array([w * rng.uniform(0.2, 0.8), h * rng.uniform(0.2, 0.8), 0])
        dist_in = rng.uniform(14, 24)
        az, el = rng.uniform(-40, 40), rng.uniform(-35, 35)
        a, e = np.radians(az), np.radians(el)
        offs = np.array([np.sin(a) * np.cos(e), np.sin(e), -np.cos(a) * np.cos(e)]) * dist_in
        R, t = look_at(target + offs, target, rng.uniform(-20, 20))
        # aim off-centre so the board covers different parts of the frame
        yaw, pitch = np.radians(rng.uniform(-12, 12)), np.radians(rng.uniform(-9, 9))
        Ry = np.array([[np.cos(yaw), 0, np.sin(yaw)], [0, 1, 0], [-np.sin(yaw), 0, np.cos(yaw)]])
        Rx = np.array([[1, 0, 0], [0, np.cos(pitch), -np.sin(pitch)], [0, np.sin(pitch), np.cos(pitch)]])
        R2 = Ry @ Rx @ R
        t2 = Ry @ Rx @ t
        img = render(canvas, K, dist, R2, t2, size)
        images.append(degrade(img, rng))
    return images


@dataclass
class Face:
    x: float
    width: float
    height: float
    value: int = 170


def default_faces() -> list[Face]:
    """Rectangles typical of bar ends and on-edge plate, left to right."""
    # (width, height, gray value, gap to the next piece; 0 = touching)
    spec = [(1.0, 2.0, 175, 0.35), (2.0, 1.0, 150, 0.35), (0.5, 0.5, 200, 0.35), (3.0, 3.0, 160, 0.35),
            (0.25, 4.0, 190, 0.35), (1.5, 1.25, 145, 0.35), (0.75, 2.5, 185, 0.4),
            (1.0, 2.0, 165, 0.0), (1.0, 2.0, 150, 0.0), (2.0, 1.5, 175, 0.35)]
    faces, x = [], 0.8
    for w, h, val, gap in spec:
        faces.append(Face(x, w, h, val))
        x += w + gap
    return faces


SHELF_GAP = 0.6  # top of the front marker row (segment y = 0) to the bottom of the faces


def front_canvas(front_rec: dict, faces: list[Face] | None = None, board_rec: dict | None = None,
                 board_at: tuple[float, float] | None = None, ppi: float = 200,
                 seams: bool = True, rng: np.random.Generator | None = None) -> tuple[Canvas, Segment]:
    """A front marker row at the plane origin with faces standing above it (all in one plane)."""
    rng = rng or np.random.default_rng(0)
    pages = targets.pages_for_print(front_rec)
    seg = Segment.from_dict(front_rec["segments"][0])
    canvas = Canvas(-6, -14, seg.width_in + 12, 18 + seg.height_in, ppi=ppi, background=45)
    # the shelf beam face the row is mounted on
    canvas.rect(-6, -SHELF_GAP, seg.width_in + 12, SHELF_GAP + seg.height_in + 1.0, 115)
    canvas.paste_segment(pages[seg.page], seg, 0, 0, pad=0.2)
    faces = faces or []
    for f in faces:
        y = -SHELF_GAP - f.height
        canvas.rect(f.x, y, f.width, f.height, f.value)
        # saw marks so faces are not perfectly flat
        n = max(2, int(f.height * 6))
        for k in range(n):
            yy = y + (k + 0.5) * f.height / n
            canvas.rect(f.x + 0.02, yy, f.width - 0.04, 0.01, max(0, f.value - 12))
    if seams:
        # a dark line centred on the boundary between touching pieces
        for a, b in zip(faces[:-1], faces[1:]):
            if abs(a.x + a.width - b.x) < 1e-6:
                h = min(a.height, b.height)
                canvas.rect(b.x - 0.015, -SHELF_GAP - h, 0.03, h, 60)
    if board_rec is not None:
        bpage = targets.pages_for_print(board_rec)[0]
        bseg = Segment.from_dict(board_rec["segments"][0])
        bx, by = board_at if board_at else (1.0, -SHELF_GAP - bseg.height_in - 0.3)
        canvas.paste_segment(bpage, bseg, bx, by, pad=0.3)
    return canvas, seg


def front_view(canvas: Canvas, target_xy, distance_in: float, az_deg: float, el_deg: float,
               K=K_TRUE, dist=DIST_TRUE, size=SIZE, roll_deg: float = 0.0):
    target = np.array([target_xy[0], target_xy[1], 0.0])
    a, e = np.radians(az_deg), np.radians(el_deg)
    offs = np.array([np.sin(a) * np.cos(e), -np.sin(e), -np.cos(a) * np.cos(e)]) * distance_in
    R, t = look_at(target + offs, target, roll_deg)
    return render(canvas, K, dist, R, t, size), R, t


def sizetest_canvas(rec: dict, ppi: float = 200) -> tuple[Canvas, list[Segment]]:
    page = targets.pages_for_print(rec)[0]
    segs = [Segment.from_dict(s) for s in rec["segments"]]
    canvas = Canvas(-2, -2, page.width + 4, page.height + 4, ppi=ppi, background=90)
    canvas.paste(targets.rasterize(page, ppi), 0, 0)
    return canvas, segs


# ---------------------------------------------------------------- whole data sets

SYNTH_MAKE = "stockscan"
SYNTH_MODEL = "synthetic"
SYNTH_LENS = "virtual lens"


def save_photo(img: np.ndarray, path, K=K_TRUE, when: str = "2026:01:01 12:00:00", quality: int = 95):
    """Write a synthetic photo as JPEG with camera EXIF."""
    from PIL import Image

    h, w = img.shape[:2]
    f35 = K[0, 0] * 43.27 / np.hypot(w, h)
    im = Image.fromarray(img)
    ex = Image.Exif()
    ex[0x010F] = SYNTH_MAKE
    ex[0x0110] = SYNTH_MODEL
    ex[0x0112] = 1
    sub = ex.get_ifd(0x8769)
    sub[0xA434] = SYNTH_LENS
    sub[0xA405] = int(round(f35))
    sub[0x9003] = when
    im.save(path, "JPEG", quality=quality, exif=ex)


def _views(canvas, target, n, rng, dist_range=(26, 36), az=35, el=(0, 25)):
    out = []
    for _ in range(n):
        img, R, t = front_view(canvas, target, rng.uniform(*dist_range), rng.uniform(-az, az),
                               rng.uniform(*el), roll_deg=rng.uniform(-4, 4))
        out.append((degrade(img, rng), R, t))
    return out


def populate(dd, n_calib: int = 12, n_front: int = 3, seed: int = 7) -> dict:
    """Generate targets and synthetic photo sets in a data directory.

    Returns {set type: set id} plus "truth": the faces.
    """
    import tempfile
    from pathlib import Path

    from . import truth as truth_mod

    rng = np.random.default_rng(seed)
    reg = dd.registry()
    board_rec = targets.generate(reg, dd.targets_dir, "calib", "letter")
    sheet_rec = targets.generate(reg, dd.targets_dir, "sizetest", "tabloid", sizes="0.75in 1in 1.5in")
    assert sheet_rec["pages"] == 1
    front_rec = targets.generate(reg, dd.targets_dir, "front", "tabloid", count=2)
    sets = {}

    def add(set_id, images, prefix):
        for k, img in enumerate(images, 1):
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, dir=dd.cache_dir) as f:
                tmp = Path(f.name)
            save_photo(img, tmp, when=f"2026:01:01 12:{k // 60:02d}:{k % 60:02d}")
            dd.add_photo(set_id, f"{prefix}_{k:03d}.JPG", tmp)

    sid = dd.create_set("synthetic", "calibration")
    add(sid, calibration_views(board_rec, n_calib, seed=seed), "CAL")
    sets["calibration"] = sid

    # plane check: board standing in the face plane above the front row
    bseg = Segment.from_dict(board_rec["segments"][0])
    canvas, seg = front_canvas(front_rec, None, board_rec, (2.0, -SHELF_GAP - bseg.height_in))
    sid = dd.create_set("synthetic", "plane_check")
    add(sid, [v[0] for v in _views(canvas, (5.0, -3.0), 3, rng, (30, 40))], "PC")
    sets["plane_check"] = sid

    # front: faces above the front row, hand measurements, clicks at the true corners
    fs = default_faces()
    canvas, seg = front_canvas(front_rec, fs, rng=rng)
    sid = dd.create_set("synthetic", "front")
    views = _views(canvas, (seg.width_in / 2 - 0.5, -1.5), n_front, rng, (34, 40), az=20, el=(5, 20))
    add(sid, [v[0] for v in views], "FR")
    truth_mod.write(dd.truth_path(sid), [truth_mod.TruthRow(i + 1, f.width, f.height) for i, f in enumerate(fs)])
    for (img, R, t), name in zip(views, dd.photos(sid)):
        clicks = []
        for f in fs:
            obj = np.array([[f.x, -SHELF_GAP - f.height, 0.0], [f.x + f.width, -SHELF_GAP, 0.0]])
            uv, _ = cv2.projectPoints(obj, cv2.Rodrigues(R)[0], t, K_TRUE, DIST_TRUE)
            uv = uv.reshape(-1, 2)
            if (uv >= 0).all() and (uv[:, 0] < SIZE[0]).all() and (uv[:, 1] < SIZE[1]).all():
                clicks.append({"tl": uv[0].round(2).tolist(), "br": uv[1].round(2).tolist()})
        dd.save_clicks(sid, name, clicks)
    sets["front"] = sid

    # size test: the sheet straight on and oblique at two distances
    canvas, _ = sizetest_canvas(sheet_rec)
    page = targets.pages_for_print(sheet_rec)[0]
    sid = dd.create_set("synthetic", "size_test")
    imgs = []
    for d in (24, 48):
        for az in (0, 45):
            img, _, _ = front_view(canvas, (page.width / 2, page.height / 2), d, az, 5)
            imgs.append(degrade(img, rng))
    add(sid, imgs, "ST")
    sets["size_test"] = sid
    sets["faces"] = fs
    return sets
