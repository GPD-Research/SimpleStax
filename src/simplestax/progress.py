"""Text progress meters: a filling bar when the total is known, a bouncing one when it is not."""

from __future__ import annotations

BAR_WIDTH = 20


def ascii_bar(fraction: float, width: int = BAR_WIDTH) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * width))
    return f"[{'#' * filled}{'-' * (width - filled)}] {int(round(fraction * 100)):3d}%"


def bounce_bar(tick: int, width: int = BAR_WIDTH, block: int = 3) -> str:
    span = width - block
    if span <= 0:
        return f"[{'#' * width}]"
    position = tick % (2 * span)
    if position > span:
        position = 2 * span - position
    return f"[{'-' * position}{'#' * block}{'-' * (span - position)}]"
