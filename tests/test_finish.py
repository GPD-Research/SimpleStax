import json

import numpy as np
import pytest

from simplestax.engines import finish as engine
from simplestax.finish import (
    FinishSettings,
    auto_settings,
    finish,
    measure,
    sample,
)
from simplestax.output import read_fits, write_fits
from simplestax.stacking import engine_command

STAR_COLOUR = np.array([1.0, 1.25, 0.8])


def synthetic_stack(seed=0, h=300, w=450, green_cast=0.0):
    """Linear sky with uneven noise per channel and coloured stars."""
    rng = np.random.default_rng(seed)
    sky = np.array([100.0, 120.0, 90.0])[:, None, None]
    noise = np.array([6.0, 4.0, 5.0])[:, None, None]
    rgb = sky + noise * rng.standard_normal((3, h, w))
    yy, xx = np.mgrid[:h, :w]
    for y, x in rng.uniform([10, 10], [h - 10, w - 10], (40, 2)):
        star = 3000 * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * 1.8**2))
        rgb += STAR_COLOUR[:, None, None] * star
    rgb[1] += green_cast * (rgb[0] + rgb[2]) / 2
    return rgb.astype(np.float32)


def test_sky_lands_neutral_at_the_target():
    rgb = synthetic_stack()
    m = measure(rgb)
    out = finish(rgb, FinishSettings(chroma_nr=0, saturation=1), m)
    sky = np.median(out.reshape(3, -1), axis=1)
    assert np.ptp(sky) < 0.02
    assert abs(sky.mean() - 0.25) < 0.04


def test_colour_balance_whitens_stars():
    rgb = synthetic_stack()
    m = measure(rgb)
    assert m.star_gains[1] < 1 < m.star_gains[2]
    bright = (rgb - np.median(rgb.reshape(3, -1), axis=1)[:, None, None]).min(axis=0) > 300
    balanced = finish(rgb, FinishSettings(chroma_nr=0, saturation=1, colour_balance=1), m)
    raw = finish(rgb, FinishSettings(chroma_nr=0, saturation=1, colour_balance=0), m)

    def spread(img):
        stars = img[:, bright].mean(axis=1)
        return np.ptp(stars) / stars.mean()

    assert spread(balanced) < spread(raw) / 2


def test_scnr_is_chosen_from_the_image_and_removes_green():
    clean = synthetic_stack()
    assert auto_settings(clean, measure(clean), "galaxy").scnr == 0
    green = synthetic_stack(green_cast=0.15)
    m = measure(green)
    m.star_gains = [1.0, 1.0, 1.0]  # keep the cast: balance alone would hide it
    settings = auto_settings(green, m, "galaxy")
    assert settings.scnr > 0.5
    settings.colour_balance = 0
    out = finish(green, settings, m)
    assert np.all(out[1] <= (out[0] + out[2]) / 2 + 1e-4 + (1 - settings.scnr))
    full = finish(green, FinishSettings(scnr=1, colour_balance=0), m)
    assert np.all(full[1] <= (full[0] + full[2]) / 2 + 1e-5)


def test_presets_follow_the_subject():
    rgb = synthetic_stack()
    m = measure(rgb)
    assert (
        auto_settings(rgb, m, "nebula").saturation > auto_settings(rgb, m, "starfield").saturation
    )


def test_preview_sample_matches_full_size():
    rgb = synthetic_stack(h=600, w=900)
    m = measure(rgb)
    small, step = sample(rgb, 60_000)
    assert step > 1
    settings = FinishSettings(chroma_nr=0)
    full_sky = np.median(finish(rgb, settings, m).reshape(3, -1), axis=1)
    small_sky = np.median(finish(small, settings, m, scale=step).reshape(3, -1), axis=1)
    assert np.allclose(full_sky, small_sky, atol=0.01)


def test_settings_round_trip_ignores_unknown_keys():
    settings = FinishSettings(scnr=0.4)
    data = settings.to_dict() | {"future_control": 3}
    assert FinishSettings.from_dict(data) == settings


def test_read_fits_round_trips_the_writer(tmp_path):
    rgb = synthetic_stack(h=20, w=30)
    write_fits(tmp_path / "s.fits", rgb, {"OBJECT": "m31", "NCOMBINE": 10})
    assert np.array_equal(read_fits(tmp_path / "s.fits"), rgb)
    (tmp_path / "bad.fits").write_bytes(b"x" * 100)
    with pytest.raises(ValueError):
        read_fits(tmp_path / "bad.fits")


def test_finish_engine_writes_tiff_and_png(tmp_path):
    rgb = synthetic_stack(h=60, w=90)
    write_fits(tmp_path / "stack.fits", rgb)
    job = {
        "fits": str(tmp_path / "stack.fits"),
        "output": str(tmp_path),
        "settings": FinishSettings().to_dict(),
        "measurements": measure(rgb).to_dict(),
    }
    messages = []
    result = engine.run(job, messages.append)
    fractions = [m["progress"] for m in messages]
    assert fractions == sorted(fractions) and fractions[-1] == 1
    import tifffile

    assert tifffile.imread(result["tiff"]).shape == (60, 90, 3)
    assert open(result["png"], "rb").read(8) == b"\x89PNG\r\n\x1a\n"
    assert json.loads((tmp_path / "finished.json").read_text())["settings"]["scnr"] == 0


def test_engine_command_names_the_engine(tmp_path):
    assert engine_command(tmp_path / "j.json", "finish")[-2:] == [
        "finish",
        str(tmp_path / "j.json"),
    ]


def test_finish_page_loads_tweaks_and_resets(qapp, tmp_path):
    from simplestax.ui.finish_page import FinishPage

    write_fits(tmp_path / "stack.fits", synthetic_stack())
    page = FinishPage()
    assert not page.export_button.isEnabled()
    page.set_stack({"fits": str(tmp_path / "stack.fits")}, "galaxy")
    assert page.auto is not None
    assert not page.preview.pixmap().isNull()
    assert page.export_button.isEnabled() and page.export_button.property("nextStep")
    assert page.settings().background == pytest.approx(page.auto.background, abs=0.01)
    page.sliders["saturation"].setValue(0)
    assert page.settings().saturation == 0
    page.reset()
    assert page.settings().saturation == pytest.approx(page.auto.saturation, abs=0.05)
    page.handle_line(json.dumps({"result": {"tiff": "/x/finished.tif", "png": "/x/finished.png"}}))
    assert "finished.tif" in page.status.text()


def test_finish_page_reports_an_unreadable_stack(qapp, tmp_path):
    from simplestax.ui.finish_page import FinishPage

    (tmp_path / "stack.fits").write_bytes(b"nope")
    page = FinishPage()
    page.set_stack({"fits": str(tmp_path / "stack.fits")}, "galaxy")
    assert page.stack is None and "Couldn't open" in page.status.text()
    assert not page.export_button.isEnabled()
