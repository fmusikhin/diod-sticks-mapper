from dsm_core.ids import new_id, new_ids
from dsm_core.model import Channel, ChannelBlock, FixtureKind, FixtureType


def ft(**kw):
    base = dict(id="ft1", name="t", kind=FixtureKind.STICK, cells=10)
    base.update(kw)
    return FixtureType(**base)


def test_channels_per_cell():
    assert ft(color_order="RGB").channels_per_cell == 3
    assert ft(color_order="RGBW", bits=16).channels_per_cell == 8
    extra = ChannelBlock((Channel("dim"), Channel("pan"), Channel("tilt")))
    assert ft(color_order="RGBW", cell_extra=extra).channels_per_cell == 7


def test_total_channels_with_head_and_tail():
    head = ChannelBlock((Channel("dim", default=255), Channel("strobe")))
    tail = ChannelBlock((Channel("mode"),))
    t = ft(kind=FixtureKind.BAR, cells=8, head=head, tail=tail)
    assert t.total_channels == 2 + 8 * 3 + 1


def test_new_id_sequence():
    assert new_id([], "fx") == "fx1"
    assert new_id(["fx1", "fx7", "ft3", "fx-bad"], "fx") == "fx8"
    assert new_ids(["fx2"], "fx", 3) == ["fx3", "fx4", "fx5"]
