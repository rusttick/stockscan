"""Photos that phones and transfers have rotated, resized, zoomed or stripped of EXIF
are still measured correctly, with the adaptation reported."""

import cv2
import numpy as np
import pytest

from stockscan import analyses, calib, faces, imageio, synth, targets, truth
from stockscan.store import DataDir


# ---------------------------------------------------------------- the camera transforms, numerically


def project(K, dist, pts):
    uv, _ = cv2.projectPoints(pts, np.zeros(3), np.zeros(3), K, dist)
    return uv.reshape(-1, 2)


@pytest.fixture(scope="module")
def points():
    rng = np.random.default_rng(0)
    return np.column_stack([rng.uniform(-12, 12, 300), rng.uniform(-9, 9, 300), rng.uniform(20, 40, 300)])


@pytest.mark.parametrize("k", [1, 2, 3])
def test_rotate_K_matches_rotated_pixels(points, k):
    K, d, (W, H) = synth.K_TRUE, synth.DIST_TRUE, synth.SIZE
    uv = project(K, d, points)
    # where np.rot90(img, k) puts each pixel
    u, v, w, h = uv[:, 0], uv[:, 1], W, H
    for _ in range(k):
        u, v, w, h = v, w - 1 - u, h, w
    K2, d2, size2 = calib.rotate_K(K, d, (W, H), k)
    # the camera turned with the image: point (X, Y) -> (Y, -X) per quarter turn
    p2 = points.copy()
    for _ in range(k):
        p2 = np.column_stack([p2[:, 1], -p2[:, 0], p2[:, 2]])
    assert size2 == (w, h)
    assert np.abs(project(K2, d2, p2) - np.column_stack([u, v])).max() < 1e-6


def test_scale_and_zoom_K(points):
    K, d, (W, H) = synth.K_TRUE, synth.DIST_TRUE, synth.SIZE
    uv = project(K, d, points)
    s = 0.37
    assert np.abs(project(calib.scale_K(K, s), d, points) - ((uv + 0.5) * s - 0.5)).max() < 1e-9
    z = 1.8
    c = np.array([(W - 1) / 2, (H - 1) / 2])
    assert np.abs(project(calib.zoom_K(K, (W, H), z), d, points) - ((uv - c) * z + c)).max() < 1e-9


def test_rotation_is_detected_from_the_image():
    """A real image rotated by 90 deg: markers found where the rotated camera predicts."""
    cal = calib.Calibration("c | l | 2000x1500", 2000, 1500, synth.K_TRUE, synth.DIST_TRUE, 0.1, 10)
    cands, how = calib.adapt(cal, (1500, 2000))
    assert len(cands) == 2 and "rotated" in how
    assert calib.adapt(cal, (1000, 750))[1] == "scaled x0.5"
    assert calib.adapt(cal, (2000, 1200)) is None  # cropped to another shape: cannot be mapped
    assert calib.adapt(cal, (2000, 1500), zoom=2.0)[1] == "digital zoom 2x"


# ---------------------------------------------------------------- end to end


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    dd = DataDir(tmp_path_factory.mktemp("tol"))
    reg = dd.registry()
    rec = targets.generate(reg, dd.targets_dir, "front", "tabloid", square="8cm", gap="2cm")
    fs = synth.default_faces()
    canvas, seg = synth.front_canvas(rec, fs)
    img, _, _ = synth.front_view(canvas, (seg.width_in / 2 - 0.5, -1.5), 30, 15, 12)
    img = synth.degrade(img, np.random.default_rng(3))
    W, H = synth.SIZE
    key = f"{synth.SYNTH_MAKE} {synth.SYNTH_MODEL} | {synth.SYNTH_LENS} | {W}x{H}"
    cal = calib.Calibration(key, W, H, synth.K_TRUE, synth.DIST_TRUE, 0.1, 20, "test")
    calib.save(cal, dd.cameras_dir)
    sid = dd.create_set("tolerance", "front")
    c = np.array([(W - 1) / 2, (H - 1) / 2])
    z = 1.5
    zoomed = cv2.warpAffine(img, np.array([[z, 0, c[0] * (1 - z)], [0, z, c[1] * (1 - z)]]), (W, H),
                            flags=cv2.INTER_CUBIC)
    variants = {
        "a_original.jpg": (img, {}),
        "b_rot90.jpg": (np.ascontiguousarray(np.rot90(img, 1)), {}),
        "c_rot270.jpg": (np.ascontiguousarray(np.rot90(img, 3)), {}),
        "d_half.jpg": (cv2.resize(img, (W // 2, H // 2), interpolation=cv2.INTER_AREA), {}),
        "e_zoom.jpg": (zoomed, {"zoom": z}),
        "f_noexif.jpg": (img, {"exif": False}),
    }
    if imageio.HEIC_OK:
        variants["g_portrait.heic"] = (img, {"orientation": 6})  # decoder will rotate it upright
    for name, (im, kw) in variants.items():
        p = dd.cache_dir / name
        synth.save_photo(im, p, **kw)
        dd.add_photo(sid, name, p)
    return dd, sid, fs, cal


EXPECT = {
    "a_original.jpg": ("exact", 0.02),
    "b_rot90.jpg": ("rotated", 0.02),
    "c_rot270.jpg": ("rotated", 0.02),
    "d_half.jpg": ("scaled x0.5", 0.04),
    "e_zoom.jpg": ("digital zoom 1.5x", 0.02),
    "f_noexif.jpg": ("set camera hint", 0.02),
    "g_portrait.heic": ("exact", 0.02),
}


def measure(ctx, name, fs):
    ph = analyses.load_photo(ctx, name)
    sd, plane = analyses.face_plane(ctx, ph)
    assert plane is not None, f"{name}: no front row pose"
    rect = analyses.straight_on(ctx, ph, sd, plane)
    res = faces.detect(rect)
    vis = [f for f in fs if f.x > rect.x0 + 0.1 and f.x + f.width < rect.x0 + rect.image.shape[1] / rect.ppi - 0.1]
    pairs = truth.align([(f.width, f.height) for f in res.faces], [(f.width, f.height) for f in vis])
    errs = [max(abs(res.faces[i].width - vis[j].width), abs(res.faces[i].height - vis[j].height))
            for i, j in pairs if i is not None and j is not None]
    return ph, len(errs), len(vis), max(errs)


def test_each_variant_is_measured(scene):
    dd, sid, fs, cal = scene
    dd.update_set(sid, camera_hint=cal.file)
    ctx = analyses.Ctx(dd, sid)
    for name in dd.photos(sid):
        how, tol = EXPECT[name]
        ph, found, total, worst = measure(ctx, name, fs)
        assert ph.cal is not None and how in ph.cam_note, (name, ph.cam_note)
        assert found == total and worst < tol, (name, found, total, worst)


def test_heic_is_stored_like_jpeg(scene):
    dd, sid, _, _ = scene
    if not imageio.HEIC_OK:
        pytest.skip("no HEIC support")
    m = imageio.read_meta(dd.photo_path(sid, "g_portrait.heic"))
    assert (m.width, m.height, m.orientation, m.derotated) == (2000, 1500, 6, True)
    a = imageio.load_gray(dd.photo_path(sid, "a_original.jpg")).astype(int)
    g = imageio.load_gray(dd.photo_path(sid, "g_portrait.heic")).astype(int)
    assert g.shape == a.shape and np.abs(g - a).mean() < 3  # same pixels, up to compression


def test_without_hint_no_calibration_and_warnings(scene):
    dd, sid, _, _ = scene
    dd.update_set(sid, camera_hint="")
    ctx = analyses.Ctx(dd, sid)
    ph = analyses.load_photo(ctx, "f_noexif.jpg")
    assert ph.cal is None and "no EXIF" in ph.cam_note
    run, line = analyses.run(dd, sid, "inspect")
    html = (dd.report_path(sid, run) / "index.html").read_text()
    assert "no camera information" in html and "different pixel sizes" in html and "digital zoom" in html
    assert "adapted=" in line
