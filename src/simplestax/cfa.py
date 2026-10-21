"""Bayer sensor data: read from a RAW file, calibrated as a grid, then turned into RGB."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

RED, GREEN, BLUE = 0, 1, 2
_CHANNEL = {"R": RED, "G": GREEN, "B": BLUE}

# Linear sRGB (D65) to CIE XYZ.
SRGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)


class UnsupportedSensor(ValueError):
    pass


@dataclass
class CfaFrame:
    """One exposure as the sensor recorded it, before any colour interpolation."""

    data: np.ndarray  # (H, W) raw DN with the black level still in; H and W are even
    pattern: np.ndarray  # (2, 2) RED / GREEN / BLUE of each site in the repeating tile
    black: np.ndarray  # (2, 2) black level of each site
    white: float
    wb: np.ndarray  # (3,) camera white balance multipliers, green = 1
    colour: np.ndarray = field(default_factory=lambda: np.eye(3, dtype=np.float32))
    """(3, 3) white-balanced camera RGB to linear sRGB."""

    @property
    def shape(self) -> tuple[int, int]:
        return self.data.shape


def camera_to_srgb(xyz_to_camera: np.ndarray) -> np.ndarray:
    """The camera's colour matrix (from rawpy/dcraw) inverted, so white stays white."""
    xyz_to_camera = np.asarray(xyz_to_camera, np.float64)[:3, :3]
    if not np.abs(xyz_to_camera).sum():
        return np.eye(3, dtype=np.float32)
    srgb_to_camera = xyz_to_camera @ SRGB_TO_XYZ
    srgb_to_camera /= srgb_to_camera.sum(axis=1, keepdims=True)
    return np.linalg.inv(srgb_to_camera).astype(np.float32)


def read_cfa(path: Path) -> CfaFrame:
    import rawpy

    with rawpy.imread(str(path)) as raw:
        desc = raw.color_desc.decode()
        colors = raw.raw_colors_visible
        if raw.raw_pattern is None or raw.raw_pattern.shape != (2, 2):
            raise UnsupportedSensor(f"{Path(path).name}: only Bayer sensors are supported")
        h, w = colors.shape
        h, w = h - h % 2, w - w % 2
        tile = colors[:2, :2]
        pattern = np.vectorize(lambda i: _CHANNEL[desc[i]])(tile).astype(np.int8)
        black = np.asarray(raw.black_level_per_channel, np.float32)[tile]
        wb = np.asarray(raw.camera_whitebalance[:3], np.float32)
        if not wb.all():
            wb = np.asarray(raw.daylight_whitebalance[:3], np.float32)
        wb = wb / wb[GREEN] if wb.all() else np.ones(3, np.float32)
        data = raw.raw_image_visible[:h, :w].astype(np.float32)
        colour = camera_to_srgb(raw.rgb_xyz_matrix)
        return CfaFrame(data, pattern, black, float(raw.white_level), wb, colour)


def tiled(values: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """A (2, 2) per-site array repeated over a full frame."""
    return np.tile(np.asarray(values, np.float32), (shape[0] // 2, shape[1] // 2))


def sites(cfa: np.ndarray) -> np.ndarray:
    """View of a frame as (H/2, 2, W/2, 2), so [:, i, :, j] is one site's plane."""
    h, w = cfa.shape
    return cfa.reshape(h // 2, 2, w // 2, 2)


def green_plane(cfa: np.ndarray, pattern: np.ndarray) -> np.ndarray:
    """Half-size image averaging the two green sites of each tile, for finding stars."""
    view = sites(cfa)
    planes = [view[:, i, :, j] for i, j in np.argwhere(pattern == GREEN)]
    return np.mean(planes, axis=0, dtype=np.float32)


def _blur121(a: np.ndarray) -> np.ndarray:
    # Reflecting at the edge keeps the Bayer parity, so masks and values stay aligned.
    p = np.pad(a, 1, mode="reflect")
    rows = p[:-2] + 2 * p[1:-1] + p[2:]
    return rows[:, :-2] + 2 * rows[:, 1:-1] + rows[:, 2:]


def demosaic(cfa: np.ndarray, pattern: np.ndarray) -> np.ndarray:
    """Bilinear interpolation of a calibrated Bayer grid into (3, H, W) RGB."""
    h, w = cfa.shape
    out = np.empty((3, h, w), np.float32)
    for channel in (RED, GREEN, BLUE):
        mask = np.tile(pattern == channel, (h // 2, w // 2))
        weight = mask.astype(np.float32)
        estimate = _blur121(cfa * weight)
        estimate /= _blur121(weight)
        out[channel] = np.where(mask, cfa, estimate)
    return out
