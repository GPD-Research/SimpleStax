from datetime import date, datetime

from simplestax.frames import FrameInfo, night_of, parse_number, parse_taken, read_info


def test_parse_number():
    assert parse_number("1/4000") == 1 / 4000
    assert parse_number("71/10") == 7.1
    assert parse_number("4") == 4.0
    assert parse_number("0") is None
    assert parse_number("1/0") is None
    assert parse_number("") is None


def test_night_rolls_over_at_noon():
    assert night_of(datetime(2026, 10, 21, 23, 50)) == date(2026, 10, 21)
    assert night_of(datetime(2026, 10, 22, 3, 10)) == date(2026, 10, 21)
    assert night_of(datetime(2026, 10, 22, 12, 0)) == date(2026, 10, 22)


def test_read_info_from_sony_tags(tmp_path):
    tags = {
        "EXIF DateTimeOriginal": "2026:10:22 01:15:00",
        "EXIF ExposureTime": "16/5",
        "EXIF ISOSpeedRatings": "1600",
        "EXIF FocalLength": "0",
        "EXIF FNumber": "0",
        "Image Model": "ILCE-6000",
    }
    info = read_info(tmp_path / "x.ARW", tags=lambda _: tags)
    assert info == FrameInfo(
        taken=parse_taken("2026:10:22 01:15:00"), exposure_s=3.2, iso=1600, camera="ILCE-6000"
    )


def test_unreadable_file_gives_empty_info(tmp_path):
    junk = tmp_path / "junk.ARW"
    junk.write_bytes(b"not a raw file")
    assert read_info(junk) == FrameInfo()
