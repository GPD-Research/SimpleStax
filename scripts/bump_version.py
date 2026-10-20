#!/usr/bin/env python3
"""
Bumps SimpleStax's version for a merged PR and closes the README's Unreleased section.

    scripts/bump_version.py minor --title "Import and frame validation" --pr 12

Prints the new version. Levels follow the project's rules:
  patch  single-component fixes, syntax corrections, UI polish, localised bug fixes
  minor  a finished multi-stage change, a new tool pipeline, a clear step up in capability
  major  a long-term goal reached, or a rewrite that changes how the app works
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "src" / "simplestax" / "__init__.py"
README = ROOT / "README.md"
LEVELS = ("patch", "minor", "major")
UNRELEASED = "## Unreleased"
_VERSION = re.compile(r'__version__ = "(\d+)\.(\d+)\.(\d+)"')


def bumped(version: tuple[int, int, int], level: str) -> tuple[int, int, int]:
    major, minor, patch = version
    if level == "major":
        return major + 1, 0, 0
    if level == "minor":
        return major, minor + 1, 0
    if level == "patch":
        return major, minor, patch + 1
    raise ValueError(f"unknown level {level!r}")


def read_version(text: str) -> tuple[int, int, int]:
    match = _VERSION.search(text)
    if not match:
        raise ValueError("no __version__ found")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def plain_text(title: str) -> str:
    """One line with Markdown syntax escaped, so a PR title can't add links or headings."""
    return re.sub(r"([\\`*_{}\[\]()<>#+!|~])", r"\\\1", " ".join(title.split()))


def close_unreleased(readme: str, version: str, date: dt.date, fallback: str) -> str:
    """
    Renames the Unreleased section to the new version and opens an empty one above it. When the
    PR wrote no notes there, [fallback] (its title) becomes the only entry.
    """
    heading = f"## Version {version} ({date:%B %Y})"
    if UNRELEASED not in readme:
        raise ValueError(f"README has no '{UNRELEASED}' section")
    start = readme.index(UNRELEASED) + len(UNRELEASED)
    following = re.search(r"^## ", readme[start:], flags=re.MULTILINE)
    end = start + following.start() if following else len(readme)
    notes = readme[start:end].strip()
    if not notes:
        notes = f"- {fallback}"
    head = readme[: readme.index(UNRELEASED)]
    return f"{head}{UNRELEASED}\n\n{heading}\n\n{notes}\n\n{readme[end:].lstrip()}".rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("level", choices=LEVELS)
    parser.add_argument("--title", required=True, help="PR title, used when it left no notes")
    parser.add_argument("--pr", type=int, help="PR number, appended to the fallback entry")
    parser.add_argument("--date", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args()

    source = VERSION_FILE.read_text()
    new = ".".join(str(part) for part in bumped(read_version(source), args.level))
    VERSION_FILE.write_text(_VERSION.sub(f'__version__ = "{new}"', source))
    title = plain_text(args.title)
    fallback = f"{title} (#{args.pr})" if args.pr else title
    README.write_text(close_unreleased(README.read_text(), new, args.date, fallback))
    print(new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
