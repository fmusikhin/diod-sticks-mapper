import pytest

from conftest import fixture, make_patch, output, rgb_type
from dsm_core.errors import OperationError
from dsm_core.model import Acceptance, AddressMode, Direction, Group, LineShape, Port, get_fixture, get_group, get_output
from dsm_core import ops
from dsm_core.schema import to_dict, validate_document


def base():
    return make_patch([rgb_type()], [fixture("fx1"), fixture("fx2", x=10.0)], [output(fixture_ids=("fx1", "fx2"))])


def test_remove_type_in_use_refused_and_patch_untouched():
    p = base()
    with pytest.raises(OperationError) as ei:
        ops.remove_fixture_type(p, "ft1")
    assert "fx1" in str(ei.value) and "fx2" in str(ei.value)
    assert p == base()


def test_remove_output_with_fixtures_refused_then_ok_after_clear():
    p = base()
    with pytest.raises(OperationError):
        ops.remove_output(p, "out1")
    p = ops.clear_address(ops.clear_address(p, "fx1"), "fx2")
    assert get_output(p, "out1").fixture_ids == ()
    p = ops.remove_output(p, "out1")
    assert p.outputs == () and get_fixture(p, "fx1").address.mode == AddressMode.UNASSIGNED


def test_add_fixture_attaches_to_port_and_remove_detaches():
    p = base()
    p = ops.add_port(p, "out1", Port(id="port1", number=1))
    fx = fixture("fx3", x=20.0, address=ops.Address(mode=AddressMode.AUTO, output_id="out1", port_id="port1"))
    p = ops.add_fixture(p, fx)
    assert get_output(p, "out1").ports[0].fixture_ids == ("fx3",)
    assert validate_document(to_dict(p)) == []
    with pytest.raises(OperationError):
        ops.remove_port(p, "out1", "port1")
    p = ops.remove_fixture(p, "fx3")
    assert get_output(p, "out1").ports[0].fixture_ids == ()
    p = ops.remove_port(p, "out1", "port1")
    assert get_output(p, "out1").ports == ()


def test_duplicate_group_block():
    p = base()
    p = ops.add_fixture(p, fixture("fx3", x=20.0))
    p = ops.add_group(p, Group(id="grp1", name="g", fixture_ids=("fx1", "fx2", "fx3")))
    assert get_fixture(p, "fx1").groups == ("grp1",)
    p, created = ops.duplicate_fixtures(p, ["fx1", "fx2", "fx3"], dx=100.0, dy=0.0, count=2)
    assert created == ["fx4", "fx5", "fx6", "fx7", "fx8", "fx9"]
    assert len(p.fixtures) == 9
    s7 = get_fixture(p, "fx7").shape
    assert isinstance(s7, LineShape) and s7.p1[0] == 200.0
    assert get_group(p, "grp1").fixture_ids == ("fx1", "fx2", "fx3") + tuple(created)
    assert get_output(p, "out1").fixture_ids == ("fx1", "fx2", "fx3") + tuple(created)
    assert get_fixture(p, "fx9").address.mode == AddressMode.AUTO
    assert validate_document(to_dict(p)) == []


def test_manual_address_and_fix_reversed():
    p = base()
    p = ops.set_manual_address(p, "fx1", "out1", universe=3, channel=100)
    a = get_fixture(p, "fx1").address
    assert a.mode == AddressMode.MANUAL and a.universe == 3 and a.channel == 100
    with pytest.raises(OperationError):
        ops.set_manual_address(p, "fx1", "out1", universe=40000, channel=1)
    with pytest.raises(OperationError):
        ops.set_manual_address(p, "fx1", "out1", universe=0, channel=513)
    p = ops.set_acceptance(p, "fx1", Acceptance.REVERSED)
    p = ops.fix_reversed(p, "fx1")
    fx = get_fixture(p, "fx1")
    assert fx.direction == Direction.REVERSE and fx.acceptance == Acceptance.NONE


def test_update_fixture_moves_between_outputs():
    p = base()
    p = ops.add_output(p, output("out2"))
    p = ops.assign_to_output(p, "fx2", "out2")
    assert get_output(p, "out1").fixture_ids == ("fx1",)
    assert get_output(p, "out2").fixture_ids == ("fx2",)
    with pytest.raises(OperationError):
        ops.update_fixture(p, "fx2", id="zzz")
    with pytest.raises(OperationError):
        ops.add_fixture(p, fixture("fx1"))


def test_group_remove_cleans_fixtures():
    p = base()
    p = ops.add_group(p, Group(id="grp1", name="g", fixture_ids=("fx1",)))
    p = ops.set_group_members(p, "grp1", ["fx2"])
    assert get_fixture(p, "fx1").groups == () and get_fixture(p, "fx2").groups == ("grp1",)
    p = ops.remove_group(p, "grp1")
    assert get_fixture(p, "fx2").groups == () and p.groups == ()
    assert ops.next_ids(p, "fixture", 2) == ["fx3", "fx4"]
