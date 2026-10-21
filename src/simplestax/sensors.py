"""Sensor sizes by manufacturer and format, and a guess at the format from an EXIF model string."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SensorSize:
    width_mm: float
    height_mm: float


FULL_FRAME = SensorSize(35.9, 24.0)

# Manufacturer -> format -> active-area size. Figures are the makers' published dimensions for
# the current generation; older bodies differ by a tenth of a millimetre at most.
SENSOR_FORMATS: dict[str, dict[str, SensorSize]] = {
    "Sony": {"APS-C": SensorSize(23.5, 15.6), "Full frame": FULL_FRAME},
    "Canon": {
        "APS-C": SensorSize(22.3, 14.9),
        "APS-H": SensorSize(27.9, 18.6),
        "Full frame": SensorSize(36.0, 24.0),
    },
    "Nikon": {
        "DX (APS-C)": SensorSize(23.5, 15.7),
        "FX (Full frame)": FULL_FRAME,
        "CX (1 inch)": SensorSize(13.2, 8.8),
    },
    "Fujifilm": {
        "X (APS-C)": SensorSize(23.5, 15.6),
        "GFX (medium format)": SensorSize(43.8, 32.9),
    },
    "Pentax": {"APS-C": SensorSize(23.5, 15.6), "Full frame": FULL_FRAME},
    "Olympus / OM System": {"Micro Four Thirds": SensorSize(17.3, 13.0)},
    "Panasonic": {"Micro Four Thirds": SensorSize(17.3, 13.0), "Full frame": FULL_FRAME},
    "Leica": {"APS-C": SensorSize(23.6, 15.7), "Full frame": FULL_FRAME},
    "Hasselblad": {"Medium format": SensorSize(43.8, 32.9)},
    "ZWO / astro cameras": {
        "IMX585 / IMX662 (1/1.2 inch)": SensorSize(11.2, 6.3),
        "IMX533 (1 inch square)": SensorSize(11.3, 11.3),
        "IMX571 (APS-C)": SensorSize(23.5, 15.7),
        "IMX455 (Full frame)": SensorSize(36.0, 24.0),
    },
}

# (manufacturer, format) picked from the EXIF "Image Model" string; first match wins.
_MODEL_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"ILCE-(1|7|9)(?!\d)|DSC-RX1(?!\d)"), "Sony", "Full frame"),
    (re.compile(r"ILCE-(3|5|6)\d{3}|NEX-|DSC-RX10"), "Sony", "APS-C"),
    (re.compile(r"EOS (R[3568]|R\b|R6|R5|RP|1D X|5D|6D)|EOS-1D X"), "Canon", "Full frame"),
    (re.compile(r"EOS 1D\b|EOS-1D Mark"), "Canon", "APS-H"),
    (re.compile(r"EOS (R(7|10|50|100)|M\d*|REBEL|Kiss|\d{2,4}D|7D|80D|90D)"), "Canon", "APS-C"),
    (
        re.compile(r"NIKON (Z ?[5-9](?!\d)|Z ?f(?![cC])|D[6-8]\d{2}(?!\d)|D[3-6](?!\d)|Df)"),
        "Nikon",
        "FX (Full frame)",
    ),
    (re.compile(r"NIKON (Z ?(30|50|fc)|D[3457]\d{3}|D[3-5]00\b)"), "Nikon", "DX (APS-C)"),
    (re.compile(r"NIKON 1\b"), "Nikon", "CX (1 inch)"),
    (re.compile(r"GFX"), "Fujifilm", "GFX (medium format)"),
    (re.compile(r"X-[A-Z]\d*|X100"), "Fujifilm", "X (APS-C)"),
    (re.compile(r"PENTAX K-1\b|K-1 Mark"), "Pentax", "Full frame"),
    (re.compile(r"PENTAX|K-\d|KP\b|K-3|K-70"), "Pentax", "APS-C"),
    (re.compile(r"E-M\d|OM-\d|PEN|E-P\d|E-PL\d"), "Olympus / OM System", "Micro Four Thirds"),
    (re.compile(r"DC-S\d"), "Panasonic", "Full frame"),
    (re.compile(r"DMC-G|DC-G"), "Panasonic", "Micro Four Thirds"),
    (re.compile(r"ASI(585|662|664)"), "ZWO / astro cameras", "IMX585 / IMX662 (1/1.2 inch)"),
    (re.compile(r"ASI533"), "ZWO / astro cameras", "IMX533 (1 inch square)"),
    (re.compile(r"ASI2600|ASI571"), "ZWO / astro cameras", "IMX571 (APS-C)"),
    (re.compile(r"ASI6200|ASI455"), "ZWO / astro cameras", "IMX455 (Full frame)"),
]


def sensor_size(manufacturer: str, sensor_format: str) -> SensorSize | None:
    return SENSOR_FORMATS.get(manufacturer, {}).get(sensor_format)


def guess_format(model: str) -> tuple[str, str] | None:
    """(manufacturer, format) for an EXIF model string such as 'ILCE-6000'; None when unknown."""
    text = " ".join(model.split()).upper()
    if not text:
        return None
    for pattern, manufacturer, sensor_format in _MODEL_RULES:
        if pattern.search(text):
            return manufacturer, sensor_format
    return None
