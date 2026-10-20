import cv2
import numpy as np

from stockscan import imageio, selftest, synth


def test_exif_roundtrip(tmp_path):
    p = tmp_path / "a.jpg"
    synth.save_photo(np.full((150, 200), 100, np.uint8), p)
    m = imageio.read_meta(p)
    assert (m.width, m.height) == (200, 150)
    assert m.make == synth.SYNTH_MAKE and m.model == synth.SYNTH_MODEL and m.lens == synth.SYNTH_LENS
    assert m.focal35 and m.datetime.startswith("2026")
    assert m.camera_key == f"{synth.SYNTH_MAKE} {synth.SYNTH_MODEL} | {synth.SYNTH_LENS} | 200x150"
    assert imageio.load_gray(p).shape == (150, 200)


def test_no_exif(tmp_path):
    from PIL import Image

    p = tmp_path / "b.png"
    Image.fromarray(np.zeros((10, 20), np.uint8)).save(p)
    m = imageio.read_meta(p)
    assert m.camera == "unknown camera" and m.orientation == 1


def test_orientation_matches_pillow():
    selftest.check_orientation()


def test_sharpness_and_clipping():
    rng = np.random.default_rng(0)
    img = (rng.random((800, 1200)) * 255).astype(np.uint8)
    blurred = cv2.GaussianBlur(img, (0, 0), 3)
    assert imageio.sharpness(img) > 10 * imageio.sharpness(blurred)
    white = np.full((10, 10), 255, np.uint8)
    assert imageio.clipped_fraction(white) == 1.0


def test_thumbnail(tmp_path):
    p = tmp_path / "a.jpg"
    synth.save_photo(np.full((600, 800), 100, np.uint8), p)
    imageio.make_thumbnail(p, tmp_path / "t" / "a.jpg")
    from PIL import Image

    assert max(Image.open(tmp_path / "t" / "a.jpg").size) == 320
