import math

import pytest

from simplestax.planning import (
    BIAS_RANGE,
    DARK_RANGE,
    FLAT_RANGE,
    crop_factor,
    master_noise_factor,
    npf_exposure,
    pixel_pitch_um,
    recommend_calibration,
    rule_500,
    shutter_at_most,
    shutter_label,
)
from simplestax.subjects import SUBJECTS, subject


def test_pixel_pitch_of_a6000():
    assert pixel_pitch_um(23.5, 6000) == pytest.approx(3.92, abs=0.01)


def test_crop_factor():
    assert crop_factor(36, 24) == pytest.approx(1.0)
    assert crop_factor(23.5, 15.6) == pytest.approx(1.53, abs=0.01)


def test_npf_is_stricter_than_500_rule_on_dense_sensor():
    npf = npf_exposure(55, 1.8, 3.92)
    assert npf == pytest.approx((35 * 1.8 + 30 * 3.92) / 55)
    assert npf < rule_500(55, 1.53)


def test_npf_grows_towards_the_pole():
    assert npf_exposure(24, 2.8, 4.0, 60) == pytest.approx(2 * npf_exposure(24, 2.8, 4.0, 0))
    assert math.isfinite(npf_exposure(24, 2.8, 4.0, 90))


def test_master_noise_factor():
    assert master_noise_factor(50, 50) == pytest.approx(math.sqrt(2))
    assert master_noise_factor(10, 0) == 1.0


@pytest.mark.parametrize("lights", [1, 10, 60, 1000])
def test_deep_sky_calibration_is_clamped(lights):
    plan = recommend_calibration(subject("nebula"), lights)
    assert DARK_RANGE[0] <= plan.darks <= DARK_RANGE[1]
    assert FLAT_RANGE[0] <= plan.flats <= FLAT_RANGE[1]
    assert BIAS_RANGE[0] <= plan.bias <= BIAS_RANGE[1]
    assert plan.total == lights + plan.darks + plan.flats + plan.bias


def test_planetary_needs_flats_only():
    plan = recommend_calibration(subject("planetary"), 5000)
    assert (plan.darks, plan.bias) == (0, 0)
    assert plan.flats > 0


def test_rejects_no_lights():
    with pytest.raises(ValueError):
        recommend_calibration(subject("galaxy"), 0)


def test_every_subject_has_a_sane_light_range():
    keys = [s.key for s in SUBJECTS]
    assert len(keys) == len(set(keys))
    assert {"planetary", "nebula", "galaxy", "starfield", "milkyway"} <= set(keys)
    for s in SUBJECTS:
        assert 1 <= s.lights[0] < s.lights[1]


@pytest.mark.parametrize(
    ("limit", "expected"), [(3.6, 3.2), (4.2, 4.0), (2.1, 2.0), (6.0, 6.0), (0.1, 0.25)]
)
def test_shutter_rounds_down_to_a_camera_step(limit, expected):
    assert shutter_at_most(limit) == expected


def test_shutter_label():
    assert shutter_label(3.2) == "3.2\u2033"
    assert shutter_label(0.25) == "1/4"
