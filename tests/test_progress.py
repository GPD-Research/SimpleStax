from simplestax.progress import ascii_bar, bounce_bar


def test_ascii_bar():
    assert ascii_bar(0.4, width=10) == "[####------]  40%"
    assert ascii_bar(-1, width=4) == "[----]   0%"
    assert ascii_bar(2, width=4) == "[####] 100%"


def test_bounce_bar_stays_inside():
    frames = {bounce_bar(t, width=10) for t in range(40)}
    assert all(len(f) == 12 and f.count("#") == 3 for f in frames)
    assert bounce_bar(0, width=10).startswith("[###")
    assert bounce_bar(7, width=10).endswith("###]")
