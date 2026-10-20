import numpy as np
import pytest

from stockscan import detect, markers, selftest, synth, targets


def test_lengths():
    assert targets.parse_length("10cm") == pytest.approx(10 / 2.54)
    assert targets.parse_length("80 mm") == pytest.approx(8 / 2.54)
    assert targets.parse_length('3.15"') == pytest.approx(3.15)
    assert targets.parse_length("2.5") == 2.5 and targets.parse_length(1.2) == 1.2
    assert targets.parse_lengths("2cm 3cm, 4 cm;5cm 1in") == pytest.approx([2 / 2.54, 3 / 2.54, 4 / 2.54, 5 / 2.54, 1])
    for bad in ("ten cm", "-3cm", "0", "3 ft", ""):
        with pytest.raises(ValueError):
            targets.parse_length(bad)
    assert targets.fmt_length(10 / 2.54) == "10.0 cm / 3.937 in"


def test_page_sizes():
    assert targets.parse_page_size("letter") == (8.5, 11.0)
    assert targets.parse_page_size("17x11") == (11.0, 17.0)
    assert targets.parse_page_size(" 8.5 X 11 ") == (8.5, 11.0)
    assert targets.parse_page_size("60x90cm") == pytest.approx((60 / 2.54, 90 / 2.54))
    assert targets.parse_page_size("A1") == pytest.approx((594 / 25.4, 841 / 25.4))
    assert targets.parse_page_size("arch-d") == (24.0, 36.0)
    for bad in ("legal-ish", "1x1", "axb", "11x17x2", "300x300"):
        with pytest.raises(ValueError):
            targets.parse_page_size(bad)


def test_generate_and_redetect(tmp_path):
    msg = selftest.check_targets(tmp_path)
    assert "front ok" in msg and "board ok" in msg


def test_charuco_board_matches_opencv(tmp_path):
    reg = targets.Registry(tmp_path / "r.yaml")
    rec = targets.generate(reg, tmp_path, "board", "tabloid", square="0.5in", rows=3, cols=8)
    seg = targets.Segment.from_dict(rec["segments"][0])
    assert (seg.squares_x, seg.squares_y) == (8, 3)
    seg.x_in = seg.y_in = 0.0
    page = targets.Page(seg.width_in, seg.height_in + 0.5)
    targets._draw_segment(page, seg)
    dpi = 160  # whole pixels everywhere: square 80 px, marker 60 px = 10 cells of 6 px
    mine = targets.rasterize(page, dpi, text=False)[: int(seg.height_in * dpi)]
    ref = targets.charuco_board(seg).generateImage((mine.shape[1], mine.shape[0]), marginSize=0, borderBits=1)
    assert np.mean(mine != ref) < 0.01


def test_front_row_geometry_matches_print(tmp_path):
    reg = targets.Registry(tmp_path / "r.yaml")
    rec = targets.generate(reg, tmp_path, "front", "tabloid", square="8cm", gap="2cm", count=2)
    seg = targets.Segment.from_dict(rec["segments"][0])
    assert seg.is_row and seg.n_markers == 4 and seg.n_corners == 16
    assert seg.marker_in == pytest.approx(8 / 2.54) and seg.gap_in == pytest.approx(2 / 2.54)
    assert seg.width_in == pytest.approx(4 * 8 / 2.54 + 3 * 2 / 2.54)
    dpi = 100
    img = targets.rasterize(targets.render(rec)[seg.page], dpi)
    corners, ids = detect.detect_markers(img)
    det = dict(zip(ids.tolist(), corners))
    obj = targets.object_points(seg)
    for k, mid in enumerate(seg.ids):
        expect = (obj[4 * k:4 * k + 4, :2] + [seg.x_in, seg.y_in]) * dpi - 0.5
        assert np.abs(det[mid] - expect).max() / dpi < 0.01  # inches, includes rasterization rounding


def test_one_id_pool_and_exhaustion(tmp_path):
    reg = targets.Registry(tmp_path / "r.yaml")
    targets.generate(reg, tmp_path, "calib", "letter")
    targets.generate(reg, tmp_path, "sizetest", "tabloid", sizes="1in 2cm")
    targets.generate(reg, tmp_path, "front", "tabloid", count=3)
    targets.generate(reg, tmp_path, "board", "arch-d", square="10cm", count=2)
    ids = [i for s in reg.segments() for i in s.ids]
    assert len(ids) == len(set(ids)) and min(ids) == 0 and max(ids) == len(ids) - 1
    m = reg.id_map()
    assert {s.kind for s in m.values()} == {"calib", "sizetest", "front", "board"}
    before = reg.ids_left()
    with pytest.raises(ValueError, match="marker IDs"):
        targets.generate(reg, tmp_path, "board", "a0", square="1cm", count=5)
    assert reg.ids_left() == before  # nothing allocated by the failed request
    reg2 = targets.Registry(tmp_path / "r.yaml")
    assert [s.serial for s in reg2.segments()] == [s.serial for s in reg.segments()]
    with pytest.raises(ValueError, match="already used"):
        targets.generate(reg2, tmp_path, "front", "letter", first_serial=1)


def test_registry_refuses_other_dictionary(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("dictionary: DICT_5X5_1000\nprints: []\n")
    with pytest.raises(ValueError, match="dictionary"):
        targets.Registry(p)


def test_calibration_board_capped(tmp_path):
    reg = targets.Registry(tmp_path / "r.yaml")
    targets.generate(reg, tmp_path, "calib", "24x36", square="0.75in")
    assert reg.segments("calib")[0].n_markers <= targets.CALIB_MAX_MARKERS


def test_boards_pack_across_pages(tmp_path):
    reg = targets.Registry(tmp_path / "r.yaml")
    rec = targets.generate(reg, tmp_path, "board", "letter", square="3cm", rows=2, cols=4, count=12)
    assert rec["pages"] > 1 and len(rec["segments"]) == 12
    assert len(targets.render(rec)) == rec["pages"]
    with pytest.raises(ValueError, match="fit"):
        targets.generate(reg, tmp_path, "board", "letter", square="10cm", rows=8)


def test_scale_bars_fit(tmp_path):
    reg = targets.Registry(tmp_path / "r.yaml")
    rec = targets.generate(reg, tmp_path, "front", "tabloid")
    assert rec["scale_bar_in"] == {"x_in": 15, "y_in": 8}


def test_fast_decoder_matches_opencv(tmp_path):
    """Our batch decoder gives OpenCV's IDs and corners (OpenCV is ~40x slower with 20k IDs)."""
    reg = targets.Registry(tmp_path / "r.yaml")
    rec = targets.generate(reg, tmp_path, "calib", "letter")
    imgs = synth.calibration_views(rec, n=3, seed=5)
    imgs.append(np.ascontiguousarray(np.rot90(imgs[0])))
    for im in imgs:
        c1, i1 = detect.detect_markers(im)
        c2, i2 = detect.detect_markers_opencv(im)
        assert sorted(i1.tolist()) == sorted(i2.tolist()) and len(i1) > 5
        ref = dict(zip(i2.tolist(), c2))
        assert max(np.abs(c - ref[i]).max() for c, i in zip(c1, i1.tolist())) < 0.05


def test_shipped_dictionary_is_reproducible():
    """The file is what the generator makes from its recorded parameters (prefix-stable)."""
    md = markers.default()
    p = markers.DEFAULT_PARAMS
    assert (md.n, md.min_distance, len(md), md.seed) == (p["n"], p["min_distance"], p["count"], p["seed"])
    head, _ = markers.generate(p["n"], p["min_distance"], 300, seed=p["seed"])
    assert np.array_equal(head.codes, md.codes[:300])
