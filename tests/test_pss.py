from pathlib import Path

import pytest

from simplestax.engines import pss


def test_command_line():
    job = pss.PssJob([Path("/n/jupiter.ser")], stack_percent=5, flat=Path("/n/flat.tif"))
    assert pss.command(job, "pss") == [
        "pss",
        "/n/jupiter.ser",
        "--out_format",
        "tiff",
        "-m",
        "Planet",
        "-s",
        "5",
        "--drizzle",
        "Off",
        "-f",
        "/n/flat.tif",
    ]


@pytest.mark.parametrize(
    "kwargs",
    [{"inputs": []}, {"stabilisation": "Moon"}, {"stack_percent": 0}, {"out_format": "jpg"}],
)
def test_job_validation(kwargs):
    with pytest.raises(ValueError):
        pss.PssJob(**{"inputs": [Path("x")], **kwargs})


def test_output_path():
    assert pss.output_path(Path("/n/frames"), "tiff") == Path("/n/frames_pss.tiff")


# Lines as PSS 0.9.8 prints them.
LOG = """\
05-21-01.2 +++ Start reading frames +++
05-21-01.2 +++ Start ranking frames +++
05-21-01.2 +++ Initializing frame alignment +++
05-21-01.2 +++ Start aligning all frames +++
05-21-01.2 +++ Start computing the reference frame +++
05-21-01.2 +++ Start creating alignment points +++
05-21-01.2 +++ Start ranking all frames at all alignment points +++
05-21-01.2 +++ Start stacking 10 frames +++
05-21-01.2 +++ Start merging all alignment patches and the background +++
05-21-01.2 +++ Start saving the stacked image +++
"""


def test_stages_are_recognised_in_order():
    stages = [pss.stage_of(line) for line in LOG.splitlines()]
    assert stages == list(range(len(pss.STAGES)))
    assert pss.stage_of("Execution over all  0.066") is None
    fractions = [pss.progress_of(line) for line in LOG.splitlines()]
    assert fractions == sorted(fractions) and fractions[0] == 0


def test_find_executable_honours_override(tmp_path, monkeypatch):
    fake = tmp_path / "PlanetarySystemStacker"
    fake.write_text("")
    monkeypatch.setenv(pss.ENV_OVERRIDE, str(fake))
    assert pss.find_executable() == str(fake)
    monkeypatch.setenv(pss.ENV_OVERRIDE, str(tmp_path / "missing"))
    assert pss.find_executable() is None
