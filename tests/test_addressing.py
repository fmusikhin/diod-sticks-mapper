import pytest

from conftest import fixture, make_patch, output, rgb_type
from dsm_core.addressing import (Segment, assign_sequential, auto_assign, auto_assign_all, layout_avoiding,
                                 layout_fixture, segments_by_fixture)
from dsm_core.errors import LayoutError
from dsm_core.model import (Address, AddressMode, Channel, ChannelBlock, FixtureKind, FixtureType, OutputRules,
                            Port, get_fixture)
from dsm_core.occupancy import Occupancy
from dsm_core.validate_types import IssueCode


def seg(u, a, b, kind="cells", c0=0, n=0, partial=False):
    return Segment(u, a, b, kind, c0, n, partial)


def out(**rules):
    return output(rules=OutputRules(**rules)) if rules else output()


# ---------- раскладка одной фикстуры ----------

def test_rgb_200_splits_170_30():
    assert layout_fixture(rgb_type(cells=200), fixture("fx1"), out(), 0, 1) == [
        seg(0, 1, 510, "cells", 0, 170), seg(1, 1, 90, "cells", 170, 30)]


def test_rgbw_130_fills_512():
    assert layout_fixture(rgb_type(cells=130, order="RGBW"), fixture("fx1"), out(), 0, 1) == [
        seg(0, 1, 512, "cells", 0, 128), seg(1, 1, 8, "cells", 128, 2)]


def test_7_channel_cells():
    extra = ChannelBlock((Channel("dim"), Channel("pan"), Channel("tilt")))
    ft = rgb_type(cells=80, order="RGBW", cell_extra=extra)
    assert ft.channels_per_cell == 7
    segs = layout_fixture(ft, fixture("fx1"), out(), 0, 1)
    assert segs[0] == seg(0, 1, 511, "cells", 0, 73)
    assert segs[1] == seg(1, 1, 49, "cells", 73, 7)


def test_skip_511_512_with_rgbw():
    segs = layout_fixture(rgb_type(cells=130, order="RGBW"), fixture("fx1"), out(skip_511_512=True), 0, 1)
    assert segs[0] == seg(0, 1, 508, "cells", 0, 127)


def test_head_and_tail_blocks():
    head = ChannelBlock((Channel("dim"), Channel("strobe"), Channel("mode")))
    ft = FixtureType(id="bar", name="bar", kind=FixtureKind.BAR, cells=170, head=head,
                     tail=ChannelBlock((Channel("x"),)))
    segs = layout_fixture(ft, fixture("fx1", "bar"), out(), 0, 1)
    assert segs == [seg(0, 1, 3, "head"), seg(0, 4, 510, "cells", 0, 169), seg(1, 1, 3, "cells", 169, 1),
                    seg(1, 4, 4, "tail")]


def test_no_auto_span_raises():
    with pytest.raises(LayoutError):
        layout_fixture(rgb_type(cells=200), fixture("fx1"), out(auto_span=False), 0, 1)
    assert layout_fixture(rgb_type(cells=170), fixture("fx1"), out(auto_span=False), 0, 1) == [
        seg(0, 1, 510, "cells", 0, 170)]


def test_whole_pixel_off_marks_partial():
    segs = layout_fixture(rgb_type(cells=200), fixture("fx1"), out(whole_pixel=False), 0, 1)
    assert segs[0] == seg(0, 1, 512, "cells", 0, 171, True)
    assert segs[1] == seg(1, 1, 88, "cells", 170, 30, True)


def test_layout_avoiding_moves_start_after_obstacle():
    occ = Occupancy()
    occ.add("out1", 0, 100, 399, "manual")
    segs = layout_avoiding(rgb_type(cells=100), fixture("fx1"), out(), 0, 1, occ)
    # препятствие не делит фикстуру: начало сдвигается за него, делит только граница юниверса
    assert segs == [seg(0, 400, 510, "cells", 0, 37), seg(1, 1, 189, "cells", 37, 63)]
    assert layout_avoiding(rgb_type(cells=33), fixture("fx1"), out(), 0, 1, occ) == [seg(0, 1, 99, "cells", 0, 33)]


def test_start_channel_not_1_and_universe_count():
    segs = layout_fixture(rgb_type(cells=10), fixture("fx1"), out(), 5, 500)
    assert segs == [seg(5, 500, 511, "cells", 0, 4), seg(6, 1, 18, "cells", 4, 6)]
    o = output(universe_count=1)
    with pytest.raises(LayoutError, match="кончились юниверсы"):
        layout_fixture(rgb_type(cells=200), fixture("fx1"), o, 0, 1)


# ---------- автоадресация ----------

def test_sticks_with_ports(sticks_patch):
    p, issues = auto_assign(sticks_patch, "out1")
    assert issues == []
    a = {f.id: (f.address.universe, f.address.channel) for f in p.fixtures}
    # порт 1: 4 × 144 RGB = 576 ячеек → юниверсы 0–3 (170 в юниверсе), порт 2 с нового юниверса 4
    assert a["fx1"] == (0, 1) and a["fx2"] == (0, 433) and a["fx3"] == (1, 355) and a["fx4"] == (2, 277)
    assert a["fx5"] == (4, 1)
    assert all(f.address.mode == AddressMode.AUTO for f in p.fixtures)
    p2, _ = auto_assign(p, "out1")
    assert p2 == p


def test_manual_is_kept_and_avoided():
    fxs = [fixture("fx1"), fixture("fx2", x=10.0, address=Address(AddressMode.MANUAL, "out1", universe=0, channel=100)),
           fixture("fx3", x=20.0)]
    p = make_patch([rgb_type(cells=50)], fxs, [output(fixture_ids=("fx1", "fx2", "fx3"))])
    p, issues = auto_assign(p, "out1")
    assert issues == []
    assert get_fixture(p, "fx2").address == Address(AddressMode.MANUAL, "out1", universe=0, channel=100)
    # fx2 вручную 100–249; fx1 (150 каналов) не влезает до 100 → начинается с 250; fx3 — с 400
    assert (get_fixture(p, "fx1").address.universe, get_fixture(p, "fx1").address.channel) == (0, 250)
    assert (get_fixture(p, "fx3").address.universe, get_fixture(p, "fx3").address.channel) == (0, 400)


def test_channel_offset_and_port_new_universe_off():
    ports = (Port(id="port1", number=1, fixture_ids=("fx1",)), Port(id="port2", number=2, fixture_ids=("fx2",), new_universe=False))
    fxs = [fixture("fx1"), fixture("fx2", x=10.0, address=Address(AddressMode.AUTO, "out1", "port2", channel_offset=10))]
    fxs[0] = fixture("fx1", address=Address(AddressMode.AUTO, "out1", "port1"))
    p = make_patch([rgb_type(cells=10)], fxs, [output(ports=ports)])
    p, issues = auto_assign(p, "out1")
    assert (get_fixture(p, "fx2").address.universe, get_fixture(p, "fx2").address.channel) == (0, 41)


def test_too_long_fixture_reported():
    p = make_patch([rgb_type(cells=200)], [fixture("fx1")], [output(fixture_ids=("fx1",), rules=OutputRules(auto_span=False))])
    p, issues = auto_assign(p, "out1")
    assert [i.code for i in issues] == [IssueCode.FIXTURE_TOO_LONG]
    assert get_fixture(p, "fx1").address.universe is None


def test_assign_sequential_block():
    fxs = [fixture(f"fx{i}", x=float(i)) for i in range(1, 5)]
    p = make_patch([rgb_type(cells=100)], fxs, [output(fixture_ids=tuple(f.id for f in fxs))])
    p, _ = auto_assign_all(p)
    p, issues = assign_sequential(p, ["fx3", "fx4"], universe=7, channel=11)
    assert issues == []
    assert get_fixture(p, "fx3").address.mode == AddressMode.MANUAL
    assert (get_fixture(p, "fx3").address.universe, get_fixture(p, "fx3").address.channel) == (7, 11)
    assert get_fixture(p, "fx4").address.mode == AddressMode.AUTO
    assert (get_fixture(p, "fx4").address.universe, get_fixture(p, "fx4").address.channel) == (7, 311)
    segs = segments_by_fixture(p)
    assert segs["fx3"][0].universe == 7 and segs["fx1"][0].universe == 0


def test_stage0_addresses(stage0_patch):
    p, issues = auto_assign_all(stage0_patch)
    assert issues == []
    segs = segments_by_fixture(p)
    assert segs["fx1"] == [seg(20, 1, 510, "cells", 0, 170), seg(21, 1, 90, "cells", 170, 30)]
    assert segs["fx2"] == [seg(20, 1, 180, "cells", 0, 60)]
    assert segs["fx3"] == [seg(20, 1, 384, "cells", 0, 128)]
