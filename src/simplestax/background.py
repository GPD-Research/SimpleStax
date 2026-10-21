"""Light-pollution gradient removal and background neutralisation on linear data."""

from __future__ import annotations

import numpy as np

GRID = 24  # boxes along the long side
DEGREE = 2
CLIP_SIGMAS = 2.5
ITERATIONS = 4


def _design(x: np.ndarray, y: np.ndarray, degree: int) -> np.ndarray:
    return np.stack([x**i * y**j for i in range(degree + 1) for j in range(degree + 1 - i)], -1)


def sample_boxes(
    channel: np.ndarray, grid: int = GRID
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Median of each box on a grid, with box centres scaled to -1..1."""
    h, w = channel.shape
    box = max(8, max(h, w) // grid)
    xs, ys, values = [], [], []
    for r0 in range(0, h - box + 1, box):
        for c0 in range(0, w - box + 1, box):
            cut = channel[r0 : r0 + box, c0 : c0 + box]
            cut = cut[np.isfinite(cut)]
            if cut.size > box * box // 2:
                xs.append((c0 + box / 2) / w * 2 - 1)
                ys.append((r0 + box / 2) / h * 2 - 1)
                values.append(np.median(cut))
    return np.array(xs), np.array(ys), np.array(values)


def fit_gradient(channel: np.ndarray, degree: int = DEGREE) -> np.ndarray:
    """A smooth surface through the sky background, ignoring boxes on stars or nebulosity."""
    x, y, v = sample_boxes(channel)
    keep = np.isfinite(v)
    a = _design(x, y, degree)
    coeffs = np.zeros(a.shape[1])
    for _ in range(ITERATIONS):
        coeffs, *_ = np.linalg.lstsq(a[keep], v[keep], rcond=None)
        residual = v - a @ coeffs
        spread = 1.4826 * np.median(np.abs(residual[keep] - np.median(residual[keep])))
        # Bright boxes (galaxies, star clusters) are cut harder than dark ones.
        keep = (residual < CLIP_SIGMAS * spread) & (residual > -2 * CLIP_SIGMAS * spread)
        if keep.sum() < a.shape[1] * 3:
            break
    h, w = channel.shape
    gx = np.linspace(-1, 1, w, dtype=np.float32) + 1 / w
    gy = np.linspace(-1, 1, h, dtype=np.float32) + 1 / h
    surface = np.zeros((h, w), np.float32)
    terms = [(i, j) for i in range(degree + 1) for j in range(degree + 1 - i)]
    for c, (i, j) in zip(coeffs, terms, strict=True):
        surface += np.float32(c) * np.outer(gy**j, gx**i)
    return surface


def flatten_background(rgb: np.ndarray, degree: int = DEGREE) -> np.ndarray:
    """Subtract each channel's gradient, leaving every channel's sky at one shared neutral level."""
    surfaces = np.stack([fit_gradient(channel, degree) for channel in rgb])
    pedestal = float(np.nanmedian(surfaces[:, ::16, ::16])) * 0.1
    return (rgb - surfaces + pedestal).astype(np.float32)
