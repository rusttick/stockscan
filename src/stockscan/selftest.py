"""Packaged synthetic self-test: proves the install works, no site data used.

Prints one line per check and finally `SS <version> T0 pass=a/b`.
"""

from __future__ import annotations

import io
import re
import shutil
import tempfile
import time
import traceback
from pathlib import Path

import cv2
import numpy as np

from . import __version__


def check_imports():
    from . import imageio

    from .web.app import package_versions

    v = package_versions()
    missing = [k for k, x in v.items() if x == "missing" and k != "pillow-heif"]
    assert not missing, f"missing packages: {missing}"
    return " ".join(f"{k}={x}" for k, x in v.items()) + ("" if imageio.HEIC_OK else " (HEIC support off)")


def check_targets(tmp: Path):
    from . import detect, targets

    reg = targets.Registry(tmp / "registry.yaml")
    out = []
    cases = [("calib", "letter", {}), ("sizetest", "a3", {"sizes": "2cm 3cm 4cm 5cm 7cm"}),
             ("front", "11x17", {"square": "8cm", "gap": "2cm", "count": 3}),
             ("board", "60x90cm", {"square": "10cm"}), ("board", "tabloid", {"square": "3cm", "rows": 3, "count": 2})]
    for item, page, kw in cases:
        rec = targets.generate(reg, tmp, item, page, **kw)
        data = (tmp / rec["file"]).read_bytes()
        assert data.startswith(b"%PDF-") and b"%%EOF" in data[-10:], "PDF malformed"
        m = re.search(rb"/MediaBox \[0 0 ([0-9.]+) ([0-9.]+)\]", data)
        assert m, "no page size in PDF"
        w, h = float(m.group(1)) / 72, float(m.group(2)) / 72
        assert abs(w - rec["page_w_in"]) < 1e-3 and abs(h - rec["page_h_in"]) < 1e-3, f"{item}: page size {w}x{h}"
        for pi, pg in enumerate(targets.pages_for_print(rec)):
            _, ids = detect.detect_markers(targets.rasterize(pg, 60 if pg.width > 20 else 120))
            expect = {i for s in rec["segments"] if s["page"] == pi for i in targets.Segment.from_dict(s).ids}
            assert expect <= set(ids.tolist()), f"{item} page {pi}: {len(expect - set(ids.tolist()))} markers not found"
        out.append(f"{item} ok")
    return ", ".join(out)


def check_orientation():
    from PIL import Image, ImageOps

    from . import imageio

    a = np.arange(12, dtype=np.uint8).reshape(3, 4) * 20
    for o in range(1, 9):
        im = Image.fromarray(a)
        ex = Image.Exif()
        ex[0x0112] = o
        b = io.BytesIO()
        im.save(b, "PNG", exif=ex.tobytes())
        b.seek(0)
        ref = np.asarray(ImageOps.exif_transpose(Image.open(b)))
        assert np.array_equal(imageio.orient(a, o), ref), f"orientation {o} differs from Pillow"
    return "8 orientations"


def _front_scene(tmp: Path, faces=None):
    from . import synth, targets

    reg = targets.Registry(tmp / "reg2.yaml")
    rec = targets.generate(reg, tmp, "front", "tabloid", square="8cm", gap="2cm")
    canvas, seg = synth.front_canvas(rec, faces)
    return reg, canvas, seg


def check_pose(tmp: Path):
    from . import detect, pose, synth

    reg, canvas, seg = _front_scene(tmp)
    worst_t = worst_r = 0.0
    rng = np.random.default_rng(3)
    for az, el, d in [(0, 10, 30), (30, 20, 40), (-35, 5, 24)]:
        img, R, t = synth.front_view(canvas, (seg.width_in / 2, 0), d, az, el)
        img = synth.degrade(img, rng)
        det = detect.detect(img, reg.id_map(), synth.K_TRUE, synth.DIST_TRUE)
        pz = pose.segment_pose(det.segments[seg.serial], synth.K_TRUE, synth.DIST_TRUE)
        assert pz is not None, "no pose"
        worst_t = max(worst_t, float(np.linalg.norm(pz.camera_center - (-R.T @ t))))
        worst_r = max(worst_r, float(np.degrees(np.linalg.norm(cv2.Rodrigues(pz.R @ R.T)[0]))))
    assert worst_t < 0.15 and worst_r < 0.3, f"pose error {worst_t:.3f} in / {worst_r:.3f} deg"
    return f"camera position err {worst_t:.3f} in, rotation err {worst_r:.3f} deg"


def check_calibration(tmp: Path):
    from . import calib, detect, synth, targets

    reg = targets.Registry(tmp / "reg3.yaml")
    rec = targets.generate(reg, tmp, "calib", "letter")
    seg = reg.segments("calib")[0]
    views = []
    for img in synth.calibration_views(rec, n=10, seed=5):
        sd = detect.detect(img, reg.id_map(), kinds=("calib",)).segments.get(seg.serial)
        if sd is not None:
            views.append((targets.object_points(seg)[sd.corner_ids], sd.corners))
    r = calib.calibrate(views, synth.SIZE)
    ef = abs(r.K[0, 0] / synth.K_TRUE[0, 0] - 1)
    ec = float(np.hypot(*(r.K[:2, 2] - synth.K_TRUE[:2, 2])))
    assert r.rms_px < 0.5 and ef < 0.01 and ec < 10, f"rms {r.rms_px:.3f} fx err {ef:.4f} c err {ec:.1f}"
    return f"rms {r.rms_px:.3f} px, fx err {ef * 100:.2f} %, centre err {ec:.1f} px"


def check_rectify(tmp: Path):
    from . import detect, faces, pose, rectify, synth, truth

    fs = synth.default_faces()
    reg, canvas, seg = _front_scene(tmp, fs)
    rng = np.random.default_rng(4)
    worst, found, total = 0.0, 0, 0
    for az, el, d in [(0, 12, 34), (25, 18, 38)]:
        img, _, _ = synth.front_view(canvas, (seg.width_in / 2 - 0.5, -1.5), d, az, el)
        img = synth.degrade(img, rng)
        det = detect.detect(img, reg.id_map(), synth.K_TRUE, synth.DIST_TRUE)
        pz = pose.segment_pose(det.segments[seg.serial], synth.K_TRUE, synth.DIST_TRUE)
        plane = rectify.FacePlane(pz, synth.K_TRUE, synth.DIST_TRUE)
        yr = rectify.face_band(seg.height_in, 6)
        xr = rectify.visible_x_range(plane, synth.SIZE, yr, (-20, seg.width_in + 20))
        rect = rectify.rectify(img, plane, xr, yr)
        res = faces.detect(rect)
        vis = [f for f in fs if f.x > xr[0] + 0.1 and f.x + f.width < xr[1] - 0.1]
        pairs = truth.align([(f.width, f.height) for f in res.faces], [(f.width, f.height) for f in vis])
        for i, j in pairs:
            if i is not None and j is not None:
                found += 1
                worst = max(worst, abs(res.faces[i].width - vis[j].width), abs(res.faces[i].height - vis[j].height))
        total += len(vis)
    assert found == total, f"faces found {found}/{total}"
    assert worst < 0.02, f"size error {worst:.3f} in"
    return f"faces {found}/{total}, max size error {worst:.4f} in"


def check_analyses(tmp: Path):
    from . import analyses, report, synth
    from .store import DataDir

    dd = DataDir(tmp / "data")
    sets = synth.populate(dd, n_calib=10, n_front=2)
    lines = {}
    for st, a in [("calibration", "calibrate"), ("calibration", "inspect"), ("plane_check", "plane_check"),
                  ("front", "manual"), ("front", "faces"), ("size_test", "size_test")]:
        _, line = analyses.run(dd, sets[st], a)
        lines[a] = report.parse_summary(line)[2]
    pc = float(lines["plane_check"]["scale_max"].rstrip("%"))
    assert pc < 0.5, f"plane check {pc} %"
    m = lines["manual"]
    assert float(m["w_max"]) < 0.02 and float(m["h_max"]) < 0.02, f"manual {m}"
    f = lines["faces"]
    a, b = f["found"].split("/")
    assert a == b, f"faces found {f['found']}"
    assert float(lines["calibrate"]["rms"]) < 0.5
    lines["rerun"] = len(analyses.rerun_all(dd, echo=lambda _l: None))
    return (f"T2 rms {lines['calibrate']['rms']}, T4 max {pc} %, T5M max {max(float(m['w_max']), float(m['h_max']))} in, "
            f"T5A found {f['found']}, rerun {lines['rerun']} reports")


def check_web(tmp: Path):
    from . import synth
    from .web.app import create_app

    app = create_app(tmp / "webdata")
    app.config["JOBS"].stop()
    c = app.test_client()
    for url in ("/", "/sets/new", "/targets", "/cameras", "/settings", "/selftest"):
        r = c.get(url)
        assert r.status_code == 200, f"GET {url}: {r.status_code}"
    r = c.post("/sets/new", data={"name": "t", "type": "front"})
    assert r.status_code == 302
    sid = r.headers["Location"].rstrip("/").split("/")[-1]
    p = tmp / "u.jpg"
    synth.save_photo(np.full((60, 80), 128, np.uint8), p)
    for expect in ("added", "duplicate"):
        with open(p, "rb") as f:
            r = c.post(f"/sets/{sid}/upload", data={"file": (f, "IMG_0001.JPG")}, content_type="multipart/form-data")
        assert r.status_code == 200 and r.get_json()["status"] == expect, f"upload: {r.get_json()}"
    assert c.get(f"/sets/{sid}").status_code == 200
    return "pages, set creation, upload, duplicate detection"


def check_dictionary():
    """The shipped dictionary: checksum, exhaustive distance check, guarantees."""
    from . import markers

    md = markers.default()
    ver = markers.verify(md)
    assert ver.duplicates == 0, "duplicate markers"
    assert ver.min_distance >= markers.DEFAULT_PARAMS["min_distance"], f"min distance {ver.min_distance}"
    g = markers.guarantees(md, ver, markers.CORRECTION_BITS, flip_ps=(0.05,))
    cv = markers.default_opencv()
    assert cv.maxCorrectionBits == markers.CORRECTION_BITS and cv.bytesList.shape[0] == len(md)
    return (f"{md.name}: {len(md)} markers, min distance {ver.min_distance} (all pairs, all rotations); "
            f"corrects {g['always_correct_up_to']}, never misreads up to {g['never_misidentified_up_to']} bad cells; "
            f"random pattern accepted {g['random_false_positive']:.1e}")


CHECKS = [
    ("imports", lambda tmp: check_imports()),
    ("dictionary", lambda tmp: check_dictionary()),
    ("targets", check_targets),
    ("orientation", lambda tmp: check_orientation()),
    ("pose", check_pose),
    ("calibration", check_calibration),
    ("rectify+faces", check_rectify),
    ("analyses", check_analyses),
    ("web", check_web),
]


def run(echo=print, include_web: bool = True) -> str:
    t0 = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="stockscan-selftest-"))
    passed = total = 0
    try:
        for name, fn in CHECKS:
            if name == "web" and not include_web:
                continue
            total += 1
            sub = tmp / name.replace("+", "_")
            sub.mkdir()
            t = time.monotonic()
            try:
                msg = fn(sub)
                passed += 1
                echo(f"PASS {name:<13} {time.monotonic() - t:5.1f}s  {msg}")
            except Exception as e:
                echo(f"FAIL {name:<13} {time.monotonic() - t:5.1f}s  {type(e).__name__}: {e}")
                echo("     " + traceback.format_exc().strip().replace("\n", "\n     "))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    line = f"SS {__version__} T0 pass={passed}/{total}"
    echo(f"total {time.monotonic() - t0:.1f}s")
    echo(line)
    return line
