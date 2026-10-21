"""Target coordinates from SIMBAD (CDS Strasbourg) over its TAP service; stdlib only."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

TAP_URL = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"
TIMEOUT_S = 10


@dataclass(frozen=True)
class Target:
    name: str
    ra_deg: float
    dec_deg: float


class LookupError(Exception):
    """SIMBAD could not be reached or answered with something unexpected."""


def _fetch(url: str) -> str:
    with urlopen(url, timeout=TIMEOUT_S) as response:
        return response.read().decode("utf-8")


def tap_url(identifier: str) -> str:
    safe = identifier.strip().replace("'", "''")
    query = (
        "SELECT TOP 1 b.main_id, b.ra, b.dec FROM basic AS b "
        f"JOIN ident AS i ON i.oidref = b.oid WHERE i.id = '{safe}'"
    )
    return (
        TAP_URL
        + "?"
        + urlencode({"request": "doQuery", "lang": "adql", "format": "json", "query": query})
    )


def resolve(identifier: str, fetch: Callable[[str], str] = _fetch) -> Target | None:
    """The catalogue position of a named object ('M42', 'NGC 7000', 'Vega'); None if unknown."""
    identifier = " ".join(identifier.split())
    if not identifier:
        return None
    try:
        payload = json.loads(fetch(tap_url(identifier)))
        rows = payload["data"]
    except (URLError, OSError, TimeoutError, ValueError, KeyError, TypeError) as error:
        raise LookupError(f"SIMBAD unreachable: {error}") from error
    if not rows:
        return None
    main_id, ra, dec = rows[0]
    if ra is None or dec is None:
        return None
    return Target(name=" ".join(str(main_id).split()), ra_deg=float(ra), dec_deg=float(dec))
