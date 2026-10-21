"""Screen stretch for previews: black point just left of the sky, midtones lifted to a target."""

from __future__ import annotations

import numpy as np

TARGET_BACKGROUND = 0.25
SHADOW_SIGMAS = 2.8


def mtf(m: float, x: np.ndarray) -> np.ndarray:
    """Midtones transfer function: 0 and 1 stay put, m maps to 0.5."""
    return ((m - 1) * x) / ((2 * m - 1) * x - m)


def midtones_for(x: float, target: float) -> float:
    """The m for which mtf(m, x) == target."""
    return x * (target - 1) / (2 * target * x - target - x)


def auto_stretch(rgb: np.ndarray, target: float = TARGET_BACKGROUND) -> np.ndarray:
    """(3, H, W) linear data to 0..1, each channel on its own so a colour cast is neutralised."""
    out = np.zeros(rgb.shape, np.float32)
    step = max(1, int(np.sqrt(rgb[0].size / 250_000)))
    for c in range(rgb.shape[0]):
        channel = rgb[c]
        sample = channel[::step, ::step]
        sample = sample[np.isfinite(sample)]
        if sample.size == 0:
            continue
        median = float(np.median(sample))
        mad = 1.4826 * float(np.median(np.abs(sample - median)))
        high = float(np.percentile(sample, 99.98))
        low = max(median - SHADOW_SIGMAS * mad, float(sample.min()))
        if high <= low:
            continue
        x = np.clip((np.nan_to_num(channel, nan=low) - low) / (high - low), 0, 1)
        background = (median - low) / (high - low)
        m = midtones_for(background, target) if 0 < background < 1 else 0.5
        out[c] = mtf(m, x)
    return out
