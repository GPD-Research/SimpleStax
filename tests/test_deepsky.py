import numpy as np
import pytest

from simplestax.cfa import GREEN, CfaFrame, demosaic, green_plane
from simplestax.engines import deepsky
from simplestax.output import write_fits, write_png
from simplestax.stacking import FrameGroup, StackJob
from simplestax.stretch import auto_stretch, midtones_for, mtf

PATTERN = np.array([[0, 1], [1, 2]], np.int8)  # RGGB
H, W = 320, 480


def sky(rng, stars, dx=0.0, dy=0.0, angle=0.0, hot=(), noise=2.0):
    """A Bayer frame of Gaussian stars shifted and rotated about the centre, plus hot pixels."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    img = np.full((H, W), 600.0, np.float32)
    c, s = np.cos(angle), np.sin(angle)
    for x, y, flux in stars:
        x0, y0 = x - W / 2, y - H / 2
        px, py = c * x0 - s * y0 + W / 2 + dx, s * x0 + c * y0 + H / 2 + dy
        img += flux * np.exp(-((xx - px) ** 2 + (yy - py) ** 2) / (2 * 2.0**2))
    img += rng.normal(0, noise, img.shape).astype(np.float32)
    for y, x in hot:
        img[y, x] = 15000
    return CfaFrame(img, PATTERN, np.full((2, 2), 512, np.float32), 16383.0, np.ones(3, np.float32))


@pytest.fixture
def field():
    rng = np.random.default_rng(1)
    stars = [
        (rng.uniform(30, W - 30), rng.uniform(30, H - 30), rng.uniform(300, 2000))
        for _ in range(60)
    ]
    return rng, stars


def test_demosaic_keeps_a_flat_colour():
    cfa = np.tile(np.array([[10, 20], [20, 30]], np.float32), (4, 6))
    rgb = demosaic(cfa, PATTERN)
    assert np.allclose(rgb[0], 10) and np.allclose(rgb[1], 20) and np.allclose(rgb[2], 30)
    assert green_plane(cfa, PATTERN).shape == (4, 6)
    assert (PATTERN == GREEN).sum() == 2


def test_find_stars_skips_hot_pixels(field):
    rng, stars = field
    frame = sky(rng, stars, hot=[(100, 100), (200, 301)])
    found = deepsky.find_stars(green_plane(frame.data - 512, PATTERN))
    assert len(found) >= 40
    hot_half = np.array([[50, 50], [150.5, 100]])
    assert min(np.hypot(*(found - h).T).min() for h in hot_half) > 2


def test_half_size_matrix_maps_full_size_pixels():
    half = np.array([[1, 0, 3.0], [0, 1, -2.0], [0, 0, 1]])
    full = deepsky.to_full_size(half)
    assert np.allclose(full[:2, 2], [6.0, -4.0])


def test_registration_recovers_a_drift(field):
    rng, stars = field
    ref = sky(rng, stars)
    moved = sky(rng, stars, dx=7.0, dy=-4.0, angle=0.004)
    a = deepsky.find_stars(green_plane(moved.data - 512, PATTERN))
    b = deepsky.find_stars(green_plane(ref.data - 512, PATTERN))
    matrix = deepsky.to_full_size(deepsky.match(a, b))
    # The moved frame's centre lands back on the reference centre.
    centre = matrix @ np.array([W / 2 + 7.0, H / 2 - 4.0, 1])
    assert np.allclose(centre[:2], [W / 2, H / 2], atol=0.5)


def test_leave_one_out_rejects_a_single_outlier_among_ten():
    frames = [np.full((3, 4, 4), 100.0, np.float32) + i % 2 for i in range(10)]
    frames[3][:, 1, 1] = 5000
    count = np.zeros((3, 4, 4), np.uint16)
    mean, m2 = np.zeros((3, 4, 4), np.float32), np.zeros((3, 4, 4), np.float32)
    for f in frames:
        deepsky.accumulate(f, count, mean, m2)
    total, kept = np.zeros_like(mean), np.zeros_like(count)
    rejected = sum(deepsky.accumulate_clipped(f, count, mean, m2, 3.0, total, kept) for f in frames)
    assert rejected == 3  # one pixel, three channels
    stacked = total / kept
    assert np.allclose(stacked[:, 1, 1], (5 * 100 + 4 * 101) / 9)
    stacked[:, 1, 1] = 100.5
    assert np.allclose(stacked, 100.5)


def test_flat_normalises_to_one_in_the_centre():
    flat = np.tile(np.array([[2000, 4000], [4000, 1000]], np.float32), (8, 8))
    norm = deepsky.normalise_flat(flat)
    assert np.allclose(norm, 1)


def test_stack_end_to_end_lights_only(field, tmp_path):
    rng, stars = field
    shifts = [(i * 3.0, -i * 2.0) for i in range(6)]
    frames = {
        f"L{i}.ARW": sky(rng, stars, dx, dy, hot=[(101, 151)]) for i, (dx, dy) in enumerate(shifts)
    }
    job = StackJob([FrameGroup("2026-10-20", list(frames))], str(tmp_path / "out"), subject="M31")
    messages = []
    result = deepsky.run(job, messages.append, reader=lambda p: frames[p.name])
    assert result["stacked"] == 6 and not result["skipped"]
    assert messages[-1]["progress"] == 1.0
    assert not (tmp_path / "out" / "work").exists()
    fits = (tmp_path / "out" / "stack.fits").read_bytes()
    assert fits.startswith(b"SIMPLE  =") and len(fits) % 2880 == 0
    assert (tmp_path / "out" / "preview.png").read_bytes().startswith(b"\x89PNG")


def test_stack_with_darks_and_flats(field, tmp_path):
    rng, stars = field
    frames = {f"L{i}.ARW": sky(rng, stars, i * 4.0, i * 1.0) for i in range(4)}
    for i in range(3):
        frames[f"D{i}.ARW"] = sky(rng, [], noise=1.0)
        frames[f"F{i}.ARW"] = sky(rng, [], noise=1.0)
        frames[f"F{i}.ARW"].data += 6000
    group = FrameGroup(
        "n",
        [f"L{i}.ARW" for i in range(4)],
        darks=["D0.ARW", "D1.ARW", "D2.ARW"],
        flats=["F0.ARW", "F1.ARW", "F2.ARW"],
    )
    result = deepsky.run(StackJob([group], str(tmp_path)), lambda m: None, lambda p: frames[p.name])
    assert result["stacked"] == 4


def test_too_few_lights_is_an_error(tmp_path):
    with pytest.raises(deepsky.EngineError):
        deepsky.run(StackJob([FrameGroup("n", ["a"])], str(tmp_path)), lambda m: None)


def test_stretch_puts_the_background_at_the_target():
    assert abs(mtf(midtones_for(0.1, 0.25), np.array(0.1)) - 0.25) < 1e-6
    rng = np.random.default_rng(0)
    rgb = rng.normal(1000, 10, (3, 50, 50)).astype(np.float32)
    rgb[:, 25, 25] = 60000
    out = auto_stretch(rgb)
    assert 0.15 < float(np.median(out)) < 0.35


def test_writers(tmp_path):
    rgb = np.zeros((3, 4, 6), np.float32)
    write_fits(tmp_path / "a.fits", rgb, {"OBJECT": "M42"})
    head = (tmp_path / "a.fits").read_bytes()[:2880].decode()
    assert "NAXIS1  =                    6" in head and "OBJECT  = 'M42     '" in head
    write_png(tmp_path / "a.png", rgb)
    assert (tmp_path / "a.png").stat().st_size > 30
