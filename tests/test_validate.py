from dataclasses import replace

from conftest import fixture, make_patch, output, rgb_type
from dsm_core.addressing import auto_assign_all
from dsm_core.model import Address, AddressMode, LineShape, OutputRules, Port, TargetKind
from dsm_core.protocol import Protocol
from dsm_core.validate import validate
from dsm_core.validate_types import IssueCode, IssueLevel


def codes(p):
    return [i.code for i in validate(p)]


def clean():
    p = make_patch([rgb_type(cells=50)], [fixture("fx1"), fixture("fx2", x=10.0)],
                   [output(ip="127.0.0.1", fixture_ids=("fx1", "fx2"))])
    return auto_assign_all(p)[0]


def test_clean_patch_has_no_issues():
    assert validate(clean()) == []


def test_channel_overlap():
    p = clean()
    p = replace(p, fixtures=(p.fixtures[0], replace(p.fixtures[1], address=Address(AddressMode.MANUAL, "out1", universe=0, channel=100))))
    iss = validate(p)
    ov = [i for i in iss if i.code == IssueCode.CHANNEL_OVERLAP]
    assert len(ov) == 1 and ov[0].channels == (100, 150) and set(ov[0].fixture_ids) == {"fx1", "fx2"}
    assert iss[0].level == IssueLevel.ERROR


def test_over_512_and_too_long():
    p = make_patch([rgb_type(cells=200)], [fixture("fx1", address=Address(AddressMode.MANUAL, "out1", universe=0, channel=1))],
                   [output(ip="127.0.0.1", fixture_ids=("fx1",), rules=OutputRules(auto_span=False))])
    assert codes(p) == [IssueCode.OVER_512]
    p2 = replace(p, fixtures=(replace(p.fixtures[0], address=Address(AddressMode.AUTO, "out1", universe=0, channel=1)),))
    assert codes(p2) == [IssueCode.FIXTURE_TOO_LONG]


def test_cell_on_boundary_manual():
    p = make_patch([rgb_type(cells=200)], [fixture("fx1", address=Address(AddressMode.MANUAL, "out1", universe=0, channel=2))],
                   [output(ip="127.0.0.1", fixture_ids=("fx1",))])
    iss = [i for i in validate(p) if i.code == IssueCode.CELL_ON_BOUNDARY]
    assert len(iss) == 1 and iss[0].level == IssueLevel.WARNING and "юниверс 1, канал 1" in iss[0].message
    p2 = replace(p, outputs=(replace(p.outputs[0], rules=OutputRules(whole_pixel=False)),))
    iss = [i for i in validate(p2) if i.code == IssueCode.CELL_ON_BOUNDARY]
    assert len(iss) == 1 and iss[0].level == IssueLevel.ERROR


def test_port_limit():
    ports = (Port(id="port1", number=1, fixture_ids=("fx1", "fx2"), max_cells=80, max_universes=1),)
    fxs = [fixture("fx1", address=Address(AddressMode.AUTO, "out1", "port1")),
           fixture("fx2", x=10.0, address=Address(AddressMode.AUTO, "out1", "port1"))]
    p = auto_assign_all(make_patch([rgb_type(cells=50)], fxs, [output(ip="127.0.0.1", ports=ports)]))[0]
    iss = [i for i in validate(p) if i.code == IssueCode.PORT_LIMIT]
    assert len(iss) == 1 and "100 ячеек" in iss[0].message
    assert iss[0].fixture_ids == ("fx2",)          # не влезла только вторая — первая выводится


def test_universe_dup_same_ip():
    p = make_patch([rgb_type(cells=10)], [fixture("fx1"), fixture("fx2", "ft1", "out2", x=10.0)],
                   [output("out1", ip="127.0.0.1", fixture_ids=("fx1",)), output("out2", ip="127.0.0.1", fixture_ids=("fx2",))])
    p = auto_assign_all(p)[0]
    assert IssueCode.UNIVERSE_DUP in codes(p)
    p3 = replace(p, outputs=(p.outputs[0], replace(p.outputs[1], ip="127.0.0.2")))
    assert IssueCode.UNIVERSE_DUP not in codes(p3)
    p4 = replace(p, outputs=(p.outputs[0], replace(p.outputs[1], start_universe=5)))
    assert IssueCode.UNIVERSE_DUP not in codes(auto_assign_all(p4)[0])


def test_universe_range_broken_ref_protocol_mismatch():
    p = clean()
    bad = replace(p.fixtures[0], address=Address(AddressMode.MANUAL, "out1", universe=40000, channel=1))
    assert IssueCode.UNIVERSE_RANGE in codes(replace(p, fixtures=(bad, p.fixtures[1])))
    assert IssueCode.BROKEN_REF in codes(replace(p, fixtures=(replace(p.fixtures[0], type_id="zzz"), p.fixtures[1])))
    out = replace(p.outputs[0], target_kind=TargetKind.MULTICAST)
    assert IssueCode.PROTOCOL_MISMATCH in codes(replace(p, outputs=(out,)))
    out = replace(p.outputs[0], protocol=Protocol.SACN, target_kind=TargetKind.BROADCAST, start_universe=1)
    assert IssueCode.PROTOCOL_MISMATCH in codes(replace(p, outputs=(out,)))


def test_target_missing_unassigned_degenerate_warnings():
    p = make_patch([rgb_type(cells=10)], [fixture("fx1"), fixture("fx2", x=10.0, address=Address())],
                   [output(fixture_ids=("fx1",))])
    p = auto_assign_all(p)[0]
    p = replace(p, fixtures=(replace(p.fixtures[0], shape=LineShape((1.0, 1.0), (1.0, 1.0))), p.fixtures[1]))
    c = codes(p)
    assert IssueCode.TARGET_MISSING in c and IssueCode.UNASSIGNED in c and IssueCode.DEGENERATE_SHAPE in c
    assert all(i.level == IssueLevel.WARNING for i in validate(p))


def test_stale_auto_after_type_change():
    p = clean()
    p = replace(p, fixture_types=(replace(p.fixture_types[0], cells=60),))
    iss = [i for i in validate(p) if i.code == IssueCode.STALE_AUTO]
    assert len(iss) == 1 and iss[0].fixture_ids == ("fx2",)
