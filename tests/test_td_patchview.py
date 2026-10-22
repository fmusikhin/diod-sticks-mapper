"""patch-view 10.2: синтетический патч стенда td/stand/patchview_test.json — состав и набор кодов проверки."""
import ipaddress
import pathlib

from dsm_core import api

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCHVIEW_TEST = ROOT / "td" / "stand" / "patchview_test.json"


def test_patchview_test_patch_composition_and_issue_codes():
    patch, notes = api.load(PATCHVIEW_TEST)
    assert notes == []
    assert api.dumps(patch) == PATCHVIEW_TEST.read_text(encoding="utf-8")   # сохранён детерминированно
    assert api.auto_assign_all(patch)[0] == patch                            # авто-адреса свежие

    assert len(patch.outputs) == 3 and [o.id for o in patch.outputs] == ["out1", "out2", "out3"]
    assert [o.protocol for o in patch.outputs] == [api.Protocol.ARTNET, api.Protocol.ARTNET, api.Protocol.SACN]
    assert all(o.target_kind == api.TargetKind.UNICAST for o in patch.outputs)
    assert all(ipaddress.ip_address(o.ip) in ipaddress.ip_network("127.0.0.0/8") for o in patch.outputs)
    out1, out2, _ = patch.outputs
    assert [p.id for p in out1.ports] == ["port1", "port2"] and out1.ports[0].new_universe
    assert out1.ports[1].max_cells is not None
    segs = api.segments_by_fixture(patch)
    used1 = {s.universe for f in patch.fixtures if f.address.output_id == "out1" for s in segs[f.id]}
    assert used1 == {0, 1, 2, 3}
    assert out1.universe_count is not None and out1.universe_count > len(used1)   # заявлено шире занятого
    assert out2.rules.skip_511_512 and not out1.rules.skip_511_512

    assert len(patch.fixtures) == 10
    modes = [f.address.mode for f in patch.fixtures]
    assert modes.count(api.AddressMode.MANUAL) == 3
    assert modes.count(api.AddressMode.UNASSIGNED) == 1
    assert modes.count(api.AddressMode.AUTO) == 6
    assert {f.shape.kind for f in patch.fixtures} == {"line", "rect"}

    issues = api.validate(patch)
    codes = {(i.level, i.code) for i in issues}
    assert codes == {(api.IssueLevel.ERROR, api.IssueCode.CHANNEL_OVERLAP),
                     (api.IssueLevel.ERROR, api.IssueCode.PORT_LIMIT),
                     (api.IssueLevel.WARNING, api.IssueCode.CELL_ON_BOUNDARY),
                     (api.IssueLevel.WARNING, api.IssueCode.UNASSIGNED)}
    assert [i.code for i in issues].count(api.IssueCode.CHANNEL_OVERLAP) == 1
    assert [i.code for i in issues].count(api.IssueCode.PORT_LIMIT) == 1
