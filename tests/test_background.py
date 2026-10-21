import numpy as np

from simplestax.background import fit_gradient, flatten_background


def test_gradient_and_colour_cast_are_removed_but_the_subject_stays():
    h, w = 240, 360
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rng = np.random.default_rng(0)
    rgb = np.stack([400 + 0.3 * yy, 600 + 0.5 * yy + 0.1 * xx, 900 + 0.2 * yy])
    rgb = rgb + rng.normal(0, 3, rgb.shape)
    galaxy = 300 * np.exp(-((xx - 180) ** 2 + (yy - 120) ** 2) / (2 * 15.0**2))
    rgb = (rgb + galaxy).astype(np.float32)
    flat = flatten_background(rgb)
    corners = flat[:, [5, 5, -5, -5], [5, -5, 5, -5]]
    assert np.ptp(np.median(flat[:, ::10, ::10], axis=(1, 2))) < 3  # neutral
    assert np.abs(corners - corners.mean()).max() < 15  # flat
    assert flat[1, 120, 180] - np.median(flat[1]) > 250  # galaxy kept


def test_a_flat_sky_fits_flat():
    surface = fit_gradient(np.full((100, 150), 7.0, np.float32))
    assert np.allclose(surface, 7.0, atol=1e-3)
