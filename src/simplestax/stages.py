"""The steps from an idea to a stacked image, in the order the app walks them."""

from __future__ import annotations

from enum import Enum


class Stage(Enum):
    PLAN = ("Plan", "What to shoot, with which lens, and how many frames of each kind.")
    SHOOT = ("Shoot", "A checklist for the night: lights first, then darks, flats and bias.")
    IMPORT = ("Import", "Bring in the frames, sort them by kind and reject unusable ones.")
    CALIBRATE = ("Calibrate", "Build master darks, flats and bias and apply them to the lights.")
    STACK = ("Stack", "Register the lights, reject outliers and combine them.")
    EXPORT = ("Export", "Write a linear TIFF or FITS for a full editor to finish.")

    def __init__(self, title: str, blurb: str) -> None:
        self.title = title
        self.blurb = blurb

    @property
    def guided(self) -> bool:
        """Steps that belong to the wizard and disappear when it is turned off."""
        return self in (Stage.PLAN, Stage.SHOOT)


def visible_stages(guided: bool) -> list[Stage]:
    return [stage for stage in Stage if guided or not stage.guided]
