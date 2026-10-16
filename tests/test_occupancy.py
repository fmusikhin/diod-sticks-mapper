from dsm_core.occupancy import Occupancy


def test_overlaps_found():
    o = Occupancy()
    o.add("out1", 0, 1, 300, "a")
    o.add("out1", 0, 301, 400, "b")
    o.add("out1", 0, 350, 500, "c")
    ov = o.overlaps()
    assert len(ov) == 1
    assert ov[0][2][2] == "b" and ov[0][3][2] == "c"


def test_next_free_skips_and_respects_limit():
    o = Occupancy()
    o.add("out1", 0, 100, 399, "manual")
    assert o.next_free("out1", 0, 1, 30, 512) == 1
    assert o.next_free("out1", 0, 90, 30, 512) == 400
    assert o.next_free("out1", 0, 400, 113, 512) == 400
    assert o.next_free("out1", 0, 400, 114, 512) is None
    assert o.next_free("out1", 0, 1, 3, 510) == 1
    assert o.next_free("out1", 1, 509, 3, 510) is None
    assert o.universes("out1") == [0]
