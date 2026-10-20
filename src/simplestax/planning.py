"""Exposure and frame-count estimates for planning a shoot."""

from __future__ import annotations

import math
from dataclasses import dataclass

from simplestax.subjects import Engine, Subject


def pixel_pitch_um(sensor_width_mm: float, image_width_px: int) -> float:
    return sensor_width_mm * 1000.0 / image_width_px


def crop_factor(sensor_width_mm: float, sensor_height_mm: float) -> float:
    return math.hypot(36.0, 24.0) / math.hypot(sensor_width_mm, sensor_height_mm)


def rule_500(focal_mm: float, crop: float = 1.0) -> float:
    """Longest untracked exposure in seconds by the old 500 rule. Too generous on modern sensors."""
    return 500.0 / (focal_mm * crop)


def npf_exposure(
    focal_mm: float, f_number: float, pixel_pitch: float, declination_deg: float = 0.0
) -> float:
    """
    Longest untracked exposure in seconds before stars visibly trail, by the simplified NPF rule
    (Michaud): (35·N + 30·p) / f. Stars near a celestial pole move slower, so the limit is
    divided by cos(declination).
    """
    limit = (35.0 * f_number + 30.0 * pixel_pitch) / focal_mm
    cos_dec = math.cos(math.radians(min(abs(declination_deg), 89.0)))
    return limit / cos_dec


def master_noise_factor(lights: int, masters: int) -> float:
    """
    How much a master calibration frame raises the stacked noise. The master's own noise is
    subtracted from every light alike, so stacking does not average it away: the result has
    variance σ²/N + σ²/M, i.e. √(1 + N/M) times the ideal. Dithering between lights spreads this
    out and makes the penalty smaller than shown.
    """
    if masters <= 0:
        return 1.0
    return math.sqrt(1.0 + lights / masters)


@dataclass(frozen=True)
class CalibrationPlan:
    lights: int
    darks: int
    flats: int
    bias: int

    @property
    def total(self) -> int:
        return self.lights + self.darks + self.flats + self.bias


# Beyond these, more calibration frames cost a lot of shooting time for little gain.
DARK_RANGE = (16, 50)
FLAT_RANGE = (20, 50)
BIAS_RANGE = (30, 100)
PLANETARY_FLATS = 50


def _clamp(value: int, bounds: tuple[int, int]) -> int:
    return max(bounds[0], min(bounds[1], value))


def recommend_calibration(subject: Subject, lights: int) -> CalibrationPlan:
    """Calibration frames to pair with [lights] light frames of [subject]."""
    if lights < 1:
        raise ValueError("lights must be at least 1")
    if subject.engine is Engine.PLANETARY:
        # Lucky imaging stacks only the best few percent and is dominated by seeing, not by
        # sensor noise; a flat is what removes dust shadows.
        return CalibrationPlan(
            lights, darks=0, flats=PLANETARY_FLATS if subject.flats else 0, bias=0
        )
    return CalibrationPlan(
        lights,
        darks=_clamp(lights, DARK_RANGE) if subject.darks else 0,
        flats=_clamp(lights, FLAT_RANGE) if subject.flats else 0,
        bias=_clamp(lights, BIAS_RANGE) if subject.bias else 0,
    )
