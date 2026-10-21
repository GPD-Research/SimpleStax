"""Writers for the stacked image: linear FITS and 16-bit TIFF for editors, PNG for previews."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

FITS_BLOCK = 2880


def _card(key: str, value: object, comment: str = "") -> str:
    if isinstance(value, bool):
        text = ("T" if value else "F").rjust(20)
    elif isinstance(value, int):
        text = str(value).rjust(20)
    elif isinstance(value, float):
        text = f"{value:.10g}".rjust(20)
    else:
        text = ("'" + str(value).replace("'", "''").ljust(8) + "'").ljust(20)
    card = f"{key:<8}= {text}" + (f" / {comment}" if comment else "")
    return card[:80].ljust(80)


def write_fits(path: Path, rgb: np.ndarray, header: dict[str, object] | None = None) -> None:
    """(3, H, W) float32 as a FITS cube, bottom row first as the standard expects."""
    _, h, w = rgb.shape
    cards = [
        _card("SIMPLE", True),
        _card("BITPIX", -32),
        _card("NAXIS", 3),
        _card("NAXIS1", w),
        _card("NAXIS2", h),
        _card("NAXIS3", 3),
    ]
    cards += [_card(key, value) for key, value in (header or {}).items()]
    cards.append("END".ljust(80))
    head = "".join(cards).encode("ascii")
    head += b" " * (-len(head) % FITS_BLOCK)
    data = np.nan_to_num(rgb[:, ::-1, :]).astype(">f4").tobytes()
    data += b"\0" * (-len(data) % FITS_BLOCK)
    Path(path).write_bytes(head + data)


def read_fits(path: Path) -> np.ndarray:
    """A (3, H, W) float cube written by `write_fits`, memory-mapped, top row first."""
    header = b""
    with open(path, "rb") as f:
        while True:
            block = f.read(FITS_BLOCK)
            if len(block) < FITS_BLOCK:
                raise ValueError(f"{Path(path).name} is not a FITS file")
            header += block
            cards = [block[i : i + 80].decode("ascii", "replace") for i in range(0, FITS_BLOCK, 80)]
            if any(card.rstrip() == "END" for card in cards):
                break
    values: dict[str, str] = {}
    for i in range(0, len(header), 80):
        card = header[i : i + 80].decode("ascii", "replace")
        if card[8:10] == "= ":
            values[card[:8].strip()] = card[10:].split("/")[0].strip()
    if values.get("BITPIX") != "-32" or values.get("NAXIS") != "3":
        raise ValueError(f"{Path(path).name} is not a SimpleStax colour stack")
    w, h, n = (int(values[f"NAXIS{i}"]) for i in (1, 2, 3))
    data = np.memmap(path, ">f4", "r", offset=len(header), shape=(n, h, w))
    return data[:, ::-1, :]


def write_tiff16(path: Path, rgb: np.ndarray, full_scale: float) -> None:
    import tifffile

    scaled = np.clip(np.nan_to_num(rgb) / full_scale, 0, 1) * 65535 + 0.5
    tifffile.imwrite(path, scaled.astype(np.uint16).transpose(1, 2, 0), photometric="rgb")


def write_png(path: Path, rgb: np.ndarray) -> None:
    """(3, H, W) values in 0..1 as an 8-bit PNG."""
    _, h, w = rgb.shape
    pixels = (np.clip(np.nan_to_num(rgb), 0, 1) * 255 + 0.5).astype(np.uint8).transpose(1, 2, 0)
    rows = np.concatenate([np.zeros((h, 1), np.uint8), pixels.reshape(h, w * 3)], axis=1)

    def chunk(tag: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)

    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows.tobytes(), 6))
        + chunk(b"IEND", b"")
    )
