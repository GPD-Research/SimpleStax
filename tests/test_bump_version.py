import datetime as dt
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "bump_version", Path(__file__).resolve().parent.parent / "scripts" / "bump_version.py"
)
bump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump)

README = """# SimpleStax

Intro.

## Unreleased

- New thing.

## Version 0.1.0 (October 2026)

- Old thing.

## Licence

GPL.
"""


@pytest.mark.parametrize(
    "level, expected",
    [("patch", (1, 2, 4)), ("minor", (1, 3, 0)), ("major", (2, 0, 0))],
)
def test_bumped(level, expected):
    assert bump.bumped((1, 2, 3), level) == expected


def test_read_version():
    assert bump.read_version('"""x"""\n\n__version__ = "0.10.2"\n') == (0, 10, 2)


def test_close_unreleased_keeps_written_notes():
    out = bump.close_unreleased(README, "0.2.0", dt.date(2026, 11, 3), "Title (#4)")
    assert (
        "## Unreleased\n\n## Version 0.2.0 (November 2026)\n\n- New thing.\n\n## Version 0.1.0"
        in out
    )
    assert "Title (#4)" not in out
    assert out.endswith("GPL.\n")


def test_close_unreleased_falls_back_to_title():
    empty = README.replace("- New thing.\n\n", "")
    out = bump.close_unreleased(empty, "0.1.1", dt.date(2026, 11, 3), "Fix label (#5)")
    assert "## Version 0.1.1 (November 2026)\n\n- Fix label (#5)\n\n## Version 0.1.0" in out


def test_close_unreleased_requires_section():
    with pytest.raises(ValueError):
        bump.close_unreleased("# x\n", "1.0.0", dt.date(2026, 1, 1), "t")
