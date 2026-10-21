"""Auto-finish: linear stack to a display image, with every automatic choice kept adjustable.

`measure()` reads the numbers the pipeline needs from the linear data once. `auto_settings()`
picks slider values for the subject from those numbers, and `finish()` applies them. The same
measurements are used for the on-screen preview and the full-size export, so they match.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

import numpy as np

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
SAMPLE_PIXELS = 1_000_000
STAR_SIGMAS = 8
# Sky colour noise (display scale) at which the preset colour-noise radius is used as is.
CHROMA_REFERENCE = 0.05


@dataclass
class Measurements:
    median: list[float]  # per-channel sky level
    mad: list[float]  # per-channel sky noise (normalised MAD)
    star_gains: list[float]  # per-channel gains that make unsaturated stars white on average
    percentiles: dict[str, float]  # luminance percentiles above the sky, for the white point

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Measurements:
        return cls(**data)


@dataclass
class FinishSettings:
    black_sigmas: float = 2.8  # black point this many sky-noise sigmas below the sky
    background: float = 0.25  # where the sky lands on the 0..1 display scale
    white: float = 99.98  # luminance percentile mapped to white
    colour_balance: float = 1.0  # 0 = as stacked, 1 = stars balanced to white
    chroma_nr: float = 1.5  # colour-noise blur radius in full-size pixels
    saturation: float = 1.2
    scnr: float = 0.0  # 0..1 strength of the green-cast removal
    sharpen: float = 0.0  # unsharp-mask amount on brightness

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> FinishSettings:
        names = {f.name for f in fields(cls)}
        return cls(**{k: float(v) for k, v in data.items() if k in names})


WHITE_PERCENTILES = (99.0, 99.5, 99.8, 99.9, 99.95, 99.98, 99.99, 99.995, 100.0)

# Per-subject starting points: (background target, saturation, SCNR ceiling, chroma NR).
# (sky brightness, saturation, SCNR ceiling, colour-noise radius, sharpening) per subject.
# A sky brightness of None leaves the tones linear: a planet is bright already.
PRESETS = {
    "galaxy": (0.25, 1.3, 1.0, 1.5, 0.0),
    "nebula": (0.25, 1.5, 0.6, 1.5, 0.0),  # OIII is teal; don't strip all of it
    "starfield": (0.18, 1.1, 1.0, 1.0, 0.0),
    "milkyway": (0.22, 1.3, 1.0, 1.5, 0.0),
    "planetary": (None, 1.2, 0.0, 0.5, 1.5),
    "lunar": (None, 1.0, 0.0, 0.5, 2.0),  # Saturation up gives the "mineral moon" colours
}
SHARPEN_RADIUS = 1.5


def sample(rgb: np.ndarray, pixels: int = SAMPLE_PIXELS) -> tuple[np.ndarray, int]:
    """Every n-th pixel each way, so the sample keeps the full-size noise; returns (sample, n)."""
    step = max(1, int(np.sqrt(rgb[0].size / pixels)))
    return np.ascontiguousarray(rgb[:, ::step, ::step], np.float32), step


def measure(rgb: np.ndarray) -> Measurements:
    rgb = np.nan_to_num(sample(rgb)[0])
    flat = rgb.reshape(3, -1)
    median = np.median(flat, axis=1)
    mad = 1.4826 * np.median(np.abs(flat - median[:, None]), axis=1)
    above = flat - median[:, None]
    luma = LUMA @ above
    percentiles = {f"{p:g}": float(np.percentile(luma, p)) for p in WHITE_PERCENTILES}
    peak = flat.max(axis=1, keepdims=True)
    # Clearly bright in every channel but unsaturated: star cores and the subject's core. Asking
    # all three channels keeps noisy single-channel spikes from biasing the balance.
    significant = np.all(above > STAR_SIGMAS * mad[:, None], axis=0)
    stars = significant & np.all(flat < 0.8 * peak, axis=0)
    gains = np.ones(3)
    if stars.sum() >= 20:
        colour = np.median(above[:, stars], axis=1)
        if np.all(colour > 0):
            gains = colour.mean() / colour
    return Measurements(
        median=median.tolist(),
        mad=np.maximum(mad, 1e-12).tolist(),
        star_gains=gains.tolist(),
        percentiles=percentiles,
    )


def white_level(m: Measurements, percentile: float) -> float:
    """Luminance above the sky at a percentile, interpolated between the measured ones."""
    keys = np.array(WHITE_PERCENTILES)
    values = np.array([m.percentiles[f"{p:g}"] for p in WHITE_PERCENTILES])
    return float(np.interp(percentile, keys, values))


def mtf(m: float, x: np.ndarray) -> np.ndarray:
    """Midtones transfer function: 0 and 1 stay put, m maps to 0.5."""
    return ((m - 1) * x) / ((2 * m - 1) * x - m)


def _blur(channel: np.ndarray, sigma: float) -> np.ndarray:
    from scipy import ndimage

    return ndimage.gaussian_filter(channel, sigma, mode="reflect")


def finish(
    rgb: np.ndarray,
    s: FinishSettings,
    m: Measurements,
    scale: float = 1.0,
    progress=None,
) -> np.ndarray:
    """(3, H, W) linear data to 0..1 display RGB. `scale` is full-size pixels per pixel of rgb."""

    def step(fraction: float, message: str) -> None:
        if progress:
            progress(fraction, message)

    median = np.array(m.median, np.float32)[:, None, None]
    mad = np.array(m.mad, np.float32)[:, None, None]
    gains = 1 + s.colour_balance * (np.array(m.star_gains, np.float32) - 1)
    gains = (gains / gains.mean())[:, None, None]

    step(0.0, "setting the black and white points")
    # Each channel's black point puts its sky at the same level after the colour gains, so the
    # sky stays neutral; one shared white point keeps the colour of everything brighter.
    noise = float(mad.mean())
    black = median - s.black_sigmas * noise / gains
    white, sky = levels(s, m)
    x = (np.nan_to_num(rgb, nan=0.0) - black) * gains / white
    np.clip(x, 0, 1, out=x)
    x = x.astype(np.float32, copy=False)

    step(0.2, "stretching")
    target = min(max(s.background, 0.01), 0.9)
    midtone = sky * (target - 1) / (2 * target * sky - target - sky)
    x = mtf(midtone, x).astype(np.float32)

    luma = np.tensordot(LUMA, x, axes=1)
    if s.sharpen > 0:
        step(0.3, "sharpening")
        detail = s.sharpen * (luma - _blur(luma, max(SHARPEN_RADIUS / scale, 0.5)))
        x += detail
        luma = luma + detail
    sigma = s.chroma_nr / scale
    if sigma > 0.3:
        step(0.4, "smoothing colour noise")
        for c in range(3):
            x[c] = luma + _blur(x[c] - luma, sigma)

    step(0.7, "saturation and green removal")
    if s.saturation != 1:
        x = luma + s.saturation * (x - luma)
    if s.scnr > 0:
        neutral = np.minimum(x[1], (x[0] + x[2]) / 2)
        x[1] += s.scnr * (neutral - x[1])
    np.clip(x, 0, 1, out=x)
    step(1.0, "done")
    return x


def levels(s: FinishSettings, m: Measurements) -> tuple[float, float]:
    """The white point above the black point, and where the sky sits on 0..1 before the stretch."""
    noise = float(np.mean(m.mad))
    white = max(white_level(m, s.white), noise * 10) + s.black_sigmas * noise
    return white, min(max(s.black_sigmas * noise / white, 1e-6), 0.99)


def green_excess(display: np.ndarray) -> float:
    """How far green sits above the red/blue average in the stars and subject, on 0..1."""
    smooth = np.stack([_blur(c, 1.5) for c in display])
    g, rb = smooth[1], (smooth[0] + smooth[2]) / 2
    # Pick signal by its faintest channel so the choice doesn't favour pixels whose green
    # happens to be high: that bias alone would read as a cast.
    faintest = smooth.min(axis=0)
    signal = faintest > np.percentile(faintest, 99)
    if not signal.any():
        return 0.0
    return float(np.mean((g - rb)[signal]))


def chroma_noise(display: np.ndarray) -> float:
    """Pixel-to-pixel colour noise in the sky background of a display image."""
    luma = np.tensordot(LUMA, display, axes=1)
    sky = luma <= np.percentile(luma, 50)  # <=: a sky clipped flat to black is still sky
    chroma = display - luma
    return float(np.mean([np.std(chroma[c][sky]) for c in range(3)]))


def auto_settings(rgb: np.ndarray, m: Measurements, subject: str = "") -> FinishSettings:
    """Slider values chosen from the subject's preset and what the image itself shows."""
    background, saturation, scnr_ceiling, chroma, sharpen = PRESETS.get(subject, PRESETS["galaxy"])
    settings = FinishSettings(saturation=saturation, chroma_nr=0, scnr=0, sharpen=sharpen)
    if background is None:
        # A planet fills the "star" sample, so balancing it to white would bleach its colour;
        # the Moon is close to grey, so balancing it to white is what neutralises it.
        settings.colour_balance = 1.0 if subject == "lunar" else 0.0
        settings.background = round(levels(settings, m)[1], 3)
    else:
        settings.background = background
    small, step = sample(rgb)
    trial = finish(small, settings, m, scale=step)
    # Green that's left after colour balance is a cast: nothing in deep sky is that green.
    settings.scnr = round(scnr_ceiling * float(np.clip(green_excess(trial) / 0.02, 0, 1)), 2)
    # More colour noise, more smoothing; clean stacks get the preset's light touch.
    noise = chroma_noise(trial)
    settings.chroma_nr = round(float(np.clip(chroma * noise / CHROMA_REFERENCE, 0.5, 4)), 1)
    return settings
