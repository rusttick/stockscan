import numpy as np
import pytest

from stockscan import store, synth, truth
from stockscan.store import DataDir


def jpg(tmp_path, name, value):
    p = tmp_path / name
    synth.save_photo(np.full((40, 60), value, np.uint8), p)
    return p


def test_safe_name():
    assert store.safe_name("../../etc/passwd") == "passwd"
    assert store.safe_name("C:\\Users\\x\\IMG 0001.JPG") == "IMG_0001.JPG"
    assert store.safe_name("...") == "file"
    assert store.safe_name("2026-10-20 09:12:00.jpg") == "2026-10-20_09_12_00.jpg"


def test_sets_and_photos(tmp_path):
    dd = DataDir(tmp_path / "d")
    sid = dd.create_set("phone 1", "calibration", "n")
    sid2 = dd.create_set("phone 1", "calibration")
    assert sid != sid2 and sid.endswith("_calibration_phone_1")
    assert dd.get_set(sid).type == "calibration" and dd.get_set(sid).notes == "n"
    assert dd.add_photo(sid, "IMG_1.JPG", jpg(tmp_path, "a.jpg", 10)) == ("added", "IMG_1.JPG")
    assert dd.add_photo(sid, "other.jpg", jpg(tmp_path, "b.jpg", 10)) == ("duplicate", "IMG_1.JPG")
    # same name in another case, different content: kept under a new name
    assert dd.add_photo(sid, "img_1.jpg", jpg(tmp_path, "c.jpg", 50)) == ("added", "img_1-2.jpg")
    assert dd.add_photo(sid, "notes.txt", jpg(tmp_path, "d.jpg", 60))[0] == "rejected"
    assert sorted(dd.photos(sid)) == ["IMG_1.JPG", "img_1-2.jpg"]
    dd.delete_photo(sid, "img_1-2.jpg")
    assert dd.photos(sid) == ["IMG_1.JPG"]
    with pytest.raises(KeyError):
        dd.get_set("../x")


def test_rescan(tmp_path):
    dd = DataDir(tmp_path / "d")
    sid = dd.create_set("s", "front")
    d = dd.photos_dir(sid)
    jpg(tmp_path, "x.jpg", 10).rename(d / "my photo.JPG")
    jpg(tmp_path, "y.jpg", 10).rename(d / "copy.jpg")
    r = dd.rescan(sid)
    assert r == {"photos": 1, "renamed": 1, "duplicates_removed": 1}


def test_truth_and_clicks(tmp_path):
    dd = DataDir(tmp_path / "d")
    sid = dd.create_set("s", "front")
    rows = [truth.TruthRow(1, 1.002, 2.004), truth.TruthRow(2, 0.252, 9.98, None, "plate")]
    truth.write(dd.truth_path(sid), rows)
    assert truth.read(dd.truth_path(sid)) == rows
    dd.save_clicks(sid, "a.jpg", [{"tl": [1, 2], "br": [3, 4]}])
    assert dd.clicks(sid)["a.jpg"][0]["br"] == [3, 4]
    dd.save_clicks(sid, "a.jpg", [])
    assert dd.clicks(sid) == {}


def test_report_dirs(tmp_path):
    dd = DataDir(tmp_path / "d")
    sid = dd.create_set("s", "front")
    a, b = dd.new_report_dir(sid, "inspect"), dd.new_report_dir(sid, "inspect")
    assert a != b and ":" not in a.name
