import base64

import numpy as np

from stockscan import synth
from stockscan.web.app import create_app


def client(path, password=None):
    app = create_app(path, password=password)
    return app, app.test_client()


def test_pages(populated):
    dd, sets = populated
    app, c = client(dd.root)
    for url in ["/", "/sets/new", "/targets", "/cameras", "/settings", "/selftest"]:
        assert c.get(url).status_code == 200, url
    for sid in (v for k, v in sets.items() if k != "faces"):
        assert c.get(f"/sets/{sid}").status_code == 200
    assert c.get("/sets/nope").status_code == 404
    assert c.get("/static/measure.js").status_code == 200
    app.config["JOBS"].stop()


def test_upload_and_truth(tmp_path):
    app, c = client(tmp_path / "d")
    r = c.post("/sets/new", data={"name": "x", "type": "front"})
    sid = r.headers["Location"].split("/")[-1]
    p = tmp_path / "a.jpg"
    synth.save_photo(np.full((60, 80), 128, np.uint8), p)
    with open(p, "rb") as f:
        r = c.post(f"/sets/{sid}/upload", data={"file": (f, "IMG 1.JPG")}, content_type="multipart/form-data")
    assert r.get_json() == {"status": "added", "name": "IMG_1.JPG"}
    r = c.post(f"/sets/{sid}/upload", data={"file": (open(p, "rb"), "x.txt")}, content_type="multipart/form-data")
    assert r.status_code == 415
    assert c.get(f"/sets/{sid}/thumbs/IMG_1.JPG").status_code == 200
    r = c.post(f"/sets/{sid}/truth", data={"index": ["1", ""], "width": ["1.0", ""], "height": ["2.0", ""],
                                           "length": ["", ""], "note": ["", ""]})
    assert "saved" in r.headers["Location"]
    assert c.get(f"/sets/{sid}/truth.csv").data.decode().splitlines()[1] == "1,1,2,,"
    app.config["JOBS"].stop()


def test_measure_roundtrip(populated):
    dd, sets = populated
    app, c = client(dd.root)
    sid = sets["front"]
    photo = dd.photos(sid)[0]
    before = dd.clicks(sid)[photo]
    d = c.get(f"/sets/{sid}/measure/{photo}/data").get_json()
    assert len(d["faces"]) == len(before) and d["front"]
    assert c.get(d["image"]).status_code == 200
    c.post(f"/sets/{sid}/measure/{photo}/data", json={"faces": d["faces"]})
    after = dd.clicks(sid)[photo]
    assert np.allclose([x["tl"] for x in after], [x["tl"] for x in before], atol=0.01)
    app.config["JOBS"].stop()


def test_job_through_web(populated):
    dd, sets = populated
    app, c = client(dd.root)
    assert c.post(f"/sets/{sets['front']}/run/faces").status_code == 302
    assert c.post(f"/sets/{sets['front']}/run/calibrate").status_code == 400  # wrong set type
    jobs = app.config["JOBS"]
    job = jobs.wait(jobs.list()[0], 60)
    assert job.status == "done" and "T5A" in job.summary
    assert c.get(f"/reports/{sets['front']}/{job.report}/").status_code == 200
    assert c.get(f"/reports/{sets['front']}/..%2F..%2Fsite.yaml/").status_code == 404
    jobs.stop()


def test_password(tmp_path):
    app, c = client(tmp_path / "d", password="s3cret")
    assert c.get("/").status_code == 401
    good = base64.b64encode(b"any:s3cret").decode()
    bad = base64.b64encode(b"any:wrong").decode()
    assert c.get("/", headers={"Authorization": f"Basic {good}"}).status_code == 200
    assert c.get("/", headers={"Authorization": f"Basic {bad}"}).status_code == 401
    app.config["JOBS"].stop()


def test_targets_and_settings(tmp_path):
    app, c = client(tmp_path / "d")
    dd = app.config["DD"]
    # the default sizes come from Settings
    r = c.post("/settings", data={"px_per_in": "100", "face_offset_in": "0.1", "above_in": "8",
                                  "blur_threshold": "60", "upload_limit_mb": "50",
                                  "ts_front_marker": "7cm", "ts_front_gap": "15mm", "ts_board_square": "9cm"})
    assert r.status_code == 302
    assert dd.site()["target_sizes"]["front_marker"] == "7cm"
    r = c.post("/targets", data={"item": "front", "page": "tabloid", "margin": "0.5in", "count": "2"})
    assert r.status_code == 302
    rec = dd.registry().prints[0]
    seg = rec["segments"][0]
    assert abs(seg["marker_in"] - 7 / 2.54) < 1e-9 and abs(seg["square_in"] - seg["marker_in"] - 1.5 / 2.54) < 1e-9
    assert c.get(f"/targets/file/{rec['file']}").data.startswith(b"%PDF")
    # explicit size on the form wins; board rows x cols
    r = c.post("/targets", data={"item": "board", "page": "custom", "page_custom": "60x90cm", "square_board": "10cm",
                                 "rows": "4", "cols_b": "5", "count": "1"})
    assert r.status_code == 302
    b = dd.registry().prints[1]["segments"][0]
    assert (b["squares_x"], b["squares_y"]) == (5, 4) and abs(b["square_in"] - 10 / 2.54) < 1e-9
    # errors are shown, nothing generated
    r = c.post("/targets", data={"item": "front", "page": "letter", "square_front": "ten"})
    assert r.status_code == 200 and b"length" in r.data and len(dd.registry().prints) == 2
    assert c.post("/settings", data={"ts_front_marker": "big"}).status_code == 200
    # print scale with a unit, labels
    r = c.post("/settings", data={"x_P0001": "38.1cm", "label_S0001": "example label"})
    assert r.status_code == 302
    site = dd.site()
    assert site.label("S0001") == "example label"
    sx, sy = site.print_scale(rec)
    assert abs(sx - (38.1 / 2.54) / 15) < 1e-6 and sx == sy
    assert "T1 P0001=15/15" in c.get("/").data.decode()
    assert c.get("/targets").status_code == 200 and c.get("/settings").status_code == 200
    app.config["JOBS"].stop()
