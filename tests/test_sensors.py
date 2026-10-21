from simplestax.sensors import SENSOR_FORMATS, guess_format, sensor_size


def test_known_formats_have_plausible_dimensions():
    for maker, formats in SENSOR_FORMATS.items():
        for name, size in formats.items():
            assert 4 < size.width_mm <= 60, (maker, name)
            assert 3 < size.height_mm <= size.width_mm + 0.01, (maker, name)
    assert sensor_size("Sony", "APS-C").width_mm == 23.5
    assert sensor_size("Sony", "nope") is None


def test_exif_models_map_to_formats():
    assert guess_format("ILCE-6000") == ("Sony", "APS-C")
    assert guess_format("ILCE-7M3") == ("Sony", "Full frame")
    assert guess_format("Canon EOS Rebel T7i") == ("Canon", "APS-C")
    assert guess_format("Canon EOS R6") == ("Canon", "Full frame")
    assert guess_format("NIKON D5600") == ("Nikon", "DX (APS-C)")
    assert guess_format("NIKON Z 6_2") == ("Nikon", "FX (Full frame)")
    assert guess_format("X-T4") == ("Fujifilm", "X (APS-C)")
    assert guess_format("E-M10MarkIII") == ("Olympus / OM System", "Micro Four Thirds")
    assert guess_format("ZWO ASI2600MC Pro") == ("ZWO / astro cameras", "IMX571 (APS-C)")
    assert guess_format("") is None
    assert guess_format("Mystery Cam 9000") is None
