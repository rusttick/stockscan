"""Photo loading, EXIF metadata, orientation, thumbnails and quality metrics.

Geometry (detection, calibration, pose) works on the pixels as stored in
the file, *before* the EXIF orientation is applied, so one calibration fits
a camera however the phone was held.  Orientation is applied only for
display (thumbnails, annotated images).

HEIC: the decoder (pillow-heif) rotates the pixels upright and reports
orientation 1, keeping the original value in im.info["original_orientation"].
That rotation is undone here, so HEIC photos look to the rest of the program
exactly like JPEGs: sensor pixels plus an orientation tag.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_OK = True
except Exception:  # missing or broken wheel: JPEG still works
    HEIC_OK = False

IMAGE_EXTS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff"}

_EXIF_IFD = 0x8769
_TAGS = {
    "make": (0x010F, False),
    "model": (0x0110, False),
    "orientation": (0x0112, False),
    "datetime": (0x9003, True),
    "exposure_s": (0x829A, True),
    "fnumber": (0x829D, True),
    "iso": (0x8827, True),
    "focal_mm": (0x920A, True),
    "focal35": (0xA405, True),
    "lens": (0xA434, True),
    "zoom": (0xA404, True),  # DigitalZoomRatio
}

# inverse of each EXIF orientation transform (display -> stored pixels)
_INVERSE = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 8, 7: 7, 8: 6}


def is_image_name(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_EXTS


@dataclass
class PhotoMeta:
    width: int  # as stored in the file
    height: int
    orientation: int = 1
    make: str = ""
    model: str = ""
    lens: str = ""
    focal_mm: float | None = None
    focal35: float | None = None
    exposure_s: float | None = None
    fnumber: float | None = None
    iso: int | None = None
    datetime: str = ""
    zoom: float | None = None  # digital zoom ratio, if recorded
    derotated: bool = False  # pixels were rotated by the decoder and turned back here

    @property
    def camera(self) -> str:
        cam = f"{self.make} {self.model}".strip()
        return cam or "unknown camera"

    @property
    def lens_name(self) -> str:
        if self.lens:
            return self.lens
        if self.focal_mm:
            return f"{self.focal_mm:g}mm"
        return "unknown lens"

    @property
    def camera_key(self) -> str:
        """Identifies a camera + lens + image size; calibrations are matched on it."""
        return f"{self.camera} | {self.lens_name} | {self.width}x{self.height}"

    @property
    def display_size(self) -> tuple[int, int]:
        if self.orientation in (5, 6, 7, 8):
            return self.height, self.width
        return self.width, self.height

    def to_dict(self) -> dict:
        d = asdict(self)
        d["camera_key"] = self.camera_key
        return d


def _clean(v):
    if isinstance(v, bytes):
        v = v.decode("ascii", "replace")
    if isinstance(v, str):
        return v.replace("\x00", "").strip()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _decoder_rotation(im) -> int:
    """Orientation the decoder already applied to the pixels (HEIC), else 1."""
    o = im.info.get("original_orientation")
    try:
        o = int(o)
    except (TypeError, ValueError):
        return 1
    return o if 2 <= o <= 8 else 1


def read_meta(path: Path) -> PhotoMeta:
    with Image.open(path) as im:
        w, h = im.size
        pre = _decoder_rotation(im)
        if pre in (5, 6, 7, 8):
            w, h = h, w
        exif = im.getexif()
        sub = exif.get_ifd(_EXIF_IFD) if exif else {}
        vals = {}
        for name, (tag, in_sub) in _TAGS.items():
            src = sub if in_sub else exif
            if tag in src:
                vals[name] = _clean(src[tag])
    meta = PhotoMeta(width=w, height=h)
    if pre != 1:
        meta.orientation, meta.derotated = pre, True
        vals.pop("orientation", None)
    for k, v in vals.items():
        if v is None or v == "":
            continue
        if k == "orientation":
            meta.orientation = int(v) if 1 <= int(v) <= 8 else 1
        elif k == "iso":
            meta.iso = int(v)
        else:
            setattr(meta, k, v)
    return meta


def load_gray(path: Path) -> np.ndarray:
    """Grayscale uint8, stored (sensor) orientation."""
    with Image.open(path) as im:
        pre = _decoder_rotation(im)
        a = np.asarray(im.convert("L"))
    return np.ascontiguousarray(orient(a, _INVERSE[pre])) if pre != 1 else a.copy()


def load_bgr(path: Path) -> np.ndarray:
    """BGR uint8, stored (sensor) orientation (for annotation)."""
    with Image.open(path) as im:
        pre = _decoder_rotation(im)
        rgb = np.asarray(im.convert("RGB"))
    if pre != 1:
        rgb = np.ascontiguousarray(orient(rgb, _INVERSE[pre]))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def orient(img: np.ndarray, orientation: int) -> np.ndarray:
    """Apply an EXIF orientation (1-8) to an image array."""
    o = orientation
    if o == 2:
        return img[:, ::-1].copy()
    if o == 3:
        return img[::-1, ::-1].copy()
    if o == 4:
        return img[::-1].copy()
    if o == 5:
        return np.ascontiguousarray(np.swapaxes(img, 0, 1))
    if o == 6:
        return np.ascontiguousarray(np.swapaxes(img, 0, 1)[:, ::-1])
    if o == 7:
        return np.ascontiguousarray(np.swapaxes(img, 0, 1)[::-1, ::-1])
    if o == 8:
        return np.ascontiguousarray(np.swapaxes(img, 0, 1)[::-1])
    return img


def save_jpeg(img: np.ndarray, path: Path, quality: int = 88, max_side: int | None = None):
    """Save a gray or BGR array as JPEG, optionally downscaled."""
    if max_side and max(img.shape[:2]) > max_side:
        s = max_side / max(img.shape[:2])
        img = cv2.resize(img, (max(1, round(img.shape[1] * s)), max(1, round(img.shape[0] * s))),
                         interpolation=cv2.INTER_AREA)
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(path, "JPEG", quality=quality)


def make_thumbnail(src: Path, dst: Path, size: int = 320):
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((size, size))
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.convert("RGB").save(dst, "JPEG", quality=80)


def sharpness(gray: np.ndarray) -> float:
    """Variance of the Laplacian on the image scaled to 1000 px long side.

    The fixed scale makes values comparable across cameras; higher is sharper.
    """
    s = 1000 / max(gray.shape)
    small = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else gray
    return float(cv2.Laplacian(small, cv2.CV_64F).var())


def clipped_fraction(gray: np.ndarray, level: int = 250) -> float:
    return float(np.count_nonzero(gray >= level) / gray.size)
