import json
import sys
from pathlib import Path

import numpy as np

from simplestax.engines import planetary, pss
from simplestax.finish import auto_settings, finish, measure
from simplestax.frames import FrameInfo, read_info
from simplestax.output import read_fits
from simplestax.stacking import FrameGroup, StackJob
from simplestax.ui.frame_picker import raw_files


def fits16(rgb: np.ndarray) -> bytes:
    """A 16-bit FITS laid out the way PSS writes it: signed with BZERO, bottom row first."""
    n, h, w = rgb.shape
    cards = [
        "SIMPLE  =                    T",
        "BITPIX  =                   16",
        "NAXIS   =                    3",
        f"NAXIS1  = {w:>20}",
        f"NAXIS2  = {h:>20}",
        f"NAXIS3  = {n:>20}",
        "BSCALE  =                    1",
        "BZERO   =                32768",
        "END",
    ]
    header = "".join(c.ljust(80) for c in cards).ljust(2880).encode()
    data = (rgb[:, ::-1, :].astype(np.int32) - 32768).astype(">i2").tobytes()
    return header + data + b"\0" * (-len(data) % 2880)


def disc(h: int = 60, w: int = 80) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    planet = np.hypot(yy - h / 2, xx - w / 2) < 20
    rgb = np.stack([planet * 50000.0, planet * 45000.0, planet * 35000.0]) + 600
    rgb[:, :10, :10] = 2000  # a marker in the top-left corner
    return rgb


def fake_pss(tmp_path: Path, monkeypatch, fail: bool = False) -> Path:
    stack = tmp_path / "stack16.fits"
    stack.write_bytes(fits16(disc()))
    script = tmp_path / "PlanetarySystemStacker"
    script.write_text(
        f"#!{sys.executable}\n"
        "import shutil, sys\n"
        "from pathlib import Path\n"
        f"stages = {list(pss.STAGES)!r}\n"
        "for s in stages: print('+++ Start ' + s + ' +++', flush=True)\n"
        + ("sys.exit(3)\n" if fail else "")
        + "src = Path(sys.argv[1])\n"
        f"shutil.copy({str(stack)!r}, src.with_name(src.stem + '_pss.fits'))\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv(pss.ENV_OVERRIDE, str(script))
    return script


def job(tmp_path: Path, *videos: str) -> StackJob:
    for name in videos:
        (tmp_path / name).write_bytes(b"video")
    lights = [str(tmp_path / name) for name in videos]
    return StackJob([FrameGroup("2026-10-21", lights)], str(tmp_path / "out"), subject="jupiter")


def test_read_fits_reads_pss_16_bit_the_right_way_up(tmp_path):
    path = tmp_path / "p.fits"
    path.write_bytes(fits16(disc()))
    rgb = read_fits(path)
    assert rgb.shape == (3, 60, 80)
    assert np.allclose(rgb[:, 5, 5], 2000) and np.allclose(rgb[0, 30, 40], 50600)


def test_engine_stacks_each_video_and_saves_it_for_finish(tmp_path, monkeypatch):
    fake_pss(tmp_path, monkeypatch)
    messages: list[dict] = []
    result = planetary.run(job(tmp_path, "jup1.avi", "jup2.mp4"), messages.append)
    assert result["engine"] == "planetary" and result["stacked"] == 2
    assert [Path(s["fits"]).name for s in result["stacks"]] == ["stack.fits", "stack_2.fits"]
    for name in ("stack.fits", "stack.tif", "stack_preview.png", "jup1_pss.fits"):
        assert (tmp_path / "out" / name).exists()
    assert not (tmp_path / "jup1_pss.fits").exists()  # moved out of the library
    rgb = read_fits(Path(result["fits"]))
    assert np.isclose(float(rgb[0, 30, 40]), 50600 / 65535)
    progress = [m["progress"] for m in messages]
    assert progress == sorted(progress) and progress[-1] == 1.0
    assert any("aligning all frames" in m["message"] for m in messages)


def test_engine_reports_failed_videos(tmp_path, monkeypatch):
    fake_pss(tmp_path, monkeypatch, fail=True)
    try:
        planetary.run(job(tmp_path, "bad.avi"), lambda m: None)
    except planetary.EngineError as error:
        assert "bad" in str(error) or "PSS" in str(error)
    else:
        raise AssertionError("expected an EngineError")


def test_engine_without_pss_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setenv(pss.ENV_OVERRIDE, str(tmp_path / "missing"))
    lines: list[str] = []
    monkeypatch.setattr("builtins.print", lambda text, **kw: lines.append(text))
    path = tmp_path / "job.json"
    path.write_text(job(tmp_path, "j.avi").to_json())
    assert planetary.main([str(path)]) == 1
    assert "Install PSS" in json.loads(lines[-1])["error"]


def test_only_video_or_image_flats_go_to_pss(tmp_path):
    j = job(tmp_path, "j.avi")
    j.groups[0].flats = ["/n/f1.ARW", "/n/flat.ser"]
    assert planetary.flat_for(j) == Path("/n/flat.ser")
    j.groups[0].flats = ["/n/f1.ARW"]
    assert planetary.flat_for(j) is None


def test_install_steps_keep_pss_in_its_own_venv(tmp_path):
    steps = planetary.install_steps("/usr/bin/python3", tmp_path / "venv")
    assert steps[0][1] == ["/usr/bin/python3", "-m", "venv", "--clear", str(tmp_path / "venv")]
    assert "--no-deps" in steps[1][1] and "planetary-system-stacker" in steps[1][1]
    assert all(cmd[0] == str(tmp_path / "venv" / "bin" / "python") for _, cmd in steps[1:])


def test_videos_are_listed_and_have_no_exif(tmp_path):
    for name in ("a.ARW", "b.mp4", "c.SER", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    assert [p.name for p in raw_files(tmp_path)] == ["a.ARW", "b.mp4", "c.SER"]
    assert read_info(tmp_path / "b.mp4", tags=lambda p: 1 / 0) == FrameInfo()


def test_planetary_finish_keeps_tones_and_colour_and_sharpens():
    rng = np.random.default_rng(0)
    rgb = disc().astype(np.float32) / 65535 + rng.normal(0, 0.002, (3, 60, 80)).astype(np.float32)
    m = measure(rgb)
    s = auto_settings(rgb, m, "planetary")
    assert s.colour_balance == 0 and s.sharpen > 0 and s.scnr == 0
    out = finish(rgb, s, m)
    r, g, b = out[:, 30, 40]
    assert r > g > b  # the planet's own colour survives
    assert float(np.median(out)) < 0.1  # the sky stays dark
