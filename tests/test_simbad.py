import json

import pytest

from simplestax.simbad import LookupError, Target, resolve, tap_url


def _answer(rows):
    return lambda url: json.dumps({"metadata": [], "data": rows})


def test_resolve_returns_main_id_and_position():
    target = resolve("m42", fetch=_answer([["M  42", 83.8201, -5.3876]]))
    assert target == Target(name="M 42", ra_deg=83.8201, dec_deg=-5.3876)


def test_unknown_object_is_none_and_blank_skips_the_network():
    assert resolve("not a thing", fetch=_answer([])) is None
    assert resolve("   ", fetch=lambda url: pytest.fail("fetched")) is None


def test_network_trouble_raises_lookup_error():
    def boom(url):
        raise OSError("offline")

    with pytest.raises(LookupError):
        resolve("M31", fetch=boom)
    with pytest.raises(LookupError):
        resolve("M31", fetch=lambda url: "<html>busy</html>")


def test_query_quotes_identifier():
    assert "i.id+%3D+%27Barnard%27%27s+Star%27" in tap_url("Barnard's Star")
