"""locate-test 2.1: разбор цели и порядок обхода (чистый модуль dsm_locate, без TouchDesigner).

Требования 1.1, 1.2, 1.3, 1.5, 1.6, 5.1–5.5, 12.1.
"""
from dataclasses import replace

import pytest

from conftest import fixture, make_patch, output, rgb_type
from dsm_core import api
from dsm_core.model import Acceptance, Address, AddressMode, Group, Port

import dsm_locate
from dsm_locate import Target, TargetError, resolve_target, step, walk_order


def _rig():
    """Риг: out1 — два порта по 4 стика (fx1–fx8); out2 — fx9; fx10 без адреса;
    out3 — fx11 и fx12 с ручным адресом внахлёст (ошибка ядра). Группы g1 (fx5, fx10, fx2) и g_empty."""
    fxs = [fixture(f"fx{i}", x=float(i * 10)) for i in range(1, 9)]
    fxs.append(fixture("fx9", output_id="out2", x=90.0))
    fxs.append(fixture("fx10", x=100.0, address=Address()))
    manual = Address(mode=AddressMode.MANUAL, output_id="out3", universe=40, channel=1)
    fxs.append(fixture("fx11", output_id="out3", x=110.0, address=manual))
    fxs.append(fixture("fx12", output_id="out3", x=120.0, address=manual))
    fxs = [replace(f, name=f"Стик {f.id[2:]}") for f in fxs]
    fxs[2] = replace(fxs[2], name="Стик, 3")                      # имя с запятой
    fxs[6] = replace(fxs[6], name="Дубль")
    fxs[7] = replace(fxs[7], name="Дубль")
    ports = (Port(id="p1", number=1, fixture_ids=("fx1", "fx2", "fx3", "fx4")),
             Port(id="p2", number=2, fixture_ids=("fx8", "fx7", "fx6", "fx5")))   # цепочка не по порядку патча
    outs = [output(ports=ports), output("out2", start=20, fixture_ids=("fx9",)),
            output("out3", start=40, fixture_ids=("fx11", "fx12")), output("out4", start=60)]
    groups = [Group(id="g1", name="g1", fixture_ids=("fx5", "fx10", "fx2")), Group(id="g_empty", name="пустая")]
    p = make_patch([rgb_type()], fxs, outs, groups)
    p, _ = api.auto_assign_all(p)
    return p, api.validate(p)


@pytest.fixture
def rig():
    return _rig()


def idx(patch, *ids):
    pos = {f.id: k for k, f in enumerate(patch.fixtures)}
    return tuple(pos[i] for i in ids)


# --- обвязка ---------------------------------------------------------------------------------

def test_constants_kept():
    assert dsm_locate.FX_COLUMNS == ["fx", "target", "number", "uni", "ch", "ncells", "offset"]
    assert dsm_locate.MODES == ("blink", "direction", "number", "address")
    assert dsm_locate.OTHERS == ("solo", "dim", "over")
    assert dsm_locate.PATTERNS == ("off", "fill", "rgbw", "chase")


def test_rig_has_core_error_and_unaddressed(rig):
    p, issues = rig
    bad = {i for iss in issues if iss.level.value == "error" for i in iss.fixture_ids}
    assert {"fx11", "fx12"} <= bad and "fx10" not in bad
    assert api.segments_by_fixture(p)["fx10"] == []


def test_target_is_frozen(rig):
    t = resolve_target(*rig, "fx1")
    with pytest.raises(Exception):
        t.spec = "x"


# --- 1.1: одна фикстура ---------------------------------------------------------------------

def test_by_id(rig):
    p, issues = rig
    t = resolve_target(p, issues, "fx7")
    assert t == Target(spec="fx7", fixtures=idx(p, "fx7"), skipped=())


def test_by_number_from_one(rig):
    p, issues = rig
    assert resolve_target(p, issues, "#1").fixtures == (0,)
    assert resolve_target(p, issues, "#12").fixtures == ()          # fx12 — ошибка ядра
    assert resolve_target(p, issues, "#9").fixtures == idx(p, "fx9")


def test_by_name(rig):
    p, issues = rig
    assert resolve_target(p, issues, "name:Стик 4").fixtures == idx(p, "fx4")
    assert resolve_target(p, issues, "name:Дубль").fixtures == idx(p, "fx7", "fx8")


def test_name_with_comma(rig):
    p, issues = rig
    assert resolve_target(p, issues, "name:Стик, 3").fixtures == idx(p, "fx3")
    assert resolve_target(p, issues, "name:Стик, 3, fx1").fixtures == idx(p, "fx1", "fx3")


def test_whitespace_trimmed(rig):
    p, issues = rig
    assert resolve_target(p, issues, "  fx2 , #1 ").fixtures == idx(p, "fx1", "fx2")


# --- 1.2: несколько, без повторов, по порядку патча ----------------------------------------

def test_list_patch_order_no_duplicates(rig):
    p, issues = rig
    t = resolve_target(p, issues, "fx6, #2, fx6, name:Стик 6, fx1")
    assert t.fixtures == idx(p, "fx1", "fx2", "fx6")
    assert t.spec == "fx6, #2, fx6, name:Стик 6, fx1"


# --- 1.3: группа, выход, порт ---------------------------------------------------------------

def test_group(rig):
    p, issues = rig
    t = resolve_target(p, issues, "group:g1")
    assert t.fixtures == idx(p, "fx2", "fx5")
    assert [i for i, _ in t.skipped] == list(idx(p, "fx10"))


def test_output(rig):
    p, issues = rig
    assert resolve_target(p, issues, "output:out1").fixtures == idx(p, *[f"fx{i}" for i in range(1, 9)])
    assert resolve_target(p, issues, "output:out2").fixtures == idx(p, "fx9")


def test_port(rig):
    p, issues = rig
    assert resolve_target(p, issues, "port:out1/p1").fixtures == idx(p, "fx1", "fx2", "fx3", "fx4")
    assert resolve_target(p, issues, "port:out1/p2").fixtures == idx(p, "fx5", "fx6", "fx7", "fx8")


def test_mixed_kinds(rig):
    p, issues = rig
    t = resolve_target(p, issues, "output:out2, port:out1/p1, group:g1")
    assert t.fixtures == idx(p, "fx1", "fx2", "fx3", "fx4", "fx5", "fx9")
    assert [i for i, _ in t.skipped] == list(idx(p, "fx10"))


# --- 1.5: не найдено ------------------------------------------------------------------------

@pytest.mark.parametrize("spec, word", [
    ("fx99", "фикстуры"),
    ("#0", "номер"),
    ("#13", "номер"),
    ("#abc", "номер"),
    ("name:Нет такой", "имен"),
    ("group:g9", "группы"),
    ("output:out9", "выхода"),
    ("port:out1/p9", "порта"),
    ("port:out9/p1", "выхода"),
    ("port:out1", "порт"),
    ("bogus:x", "вид"),
])
def test_unknown_element(rig, spec, word):
    p, issues = rig
    with pytest.raises(TargetError) as e:
        resolve_target(p, issues, spec)
    assert isinstance(e.value, ValueError)
    assert e.value.spec == spec
    assert word in e.value.reason
    assert e.value.reason in str(e.value)


def test_unknown_element_in_list_fails_whole_spec(rig):
    p, issues = rig
    with pytest.raises(TargetError) as e:
        resolve_target(p, issues, "fx1, fx99")
    assert "fx99" in e.value.reason and e.value.spec == "fx1, fx99"


def test_unknown_reason_text(rig):
    p, issues = rig
    with pytest.raises(TargetError) as e:
        resolve_target(p, issues, "fx99")
    assert e.value.reason == "нет такой фикстуры: fx99"


@pytest.mark.parametrize("spec", ["", "   ", ",", " , ,"])
def test_empty_spec(rig, spec):
    p, issues = rig
    with pytest.raises(TargetError) as e:
        resolve_target(p, issues, spec)
    assert e.value.reason == "пустая цель"


@pytest.mark.parametrize("spec", ["group:g_empty", "output:out4"])
def test_empty_container_is_error(rig, spec):
    p, issues = rig
    with pytest.raises(TargetError) as e:
        resolve_target(p, issues, spec)
    assert "нет фикстур" in e.value.reason


# --- 1.6: не выводится ----------------------------------------------------------------------

def test_unaddressed_fixture_skipped(rig):
    p, issues = rig
    t = resolve_target(p, issues, "fx10")
    assert t.fixtures == ()
    ((i, reason),) = t.skipped
    assert i == idx(p, "fx10")[0] and "нет адреса" in reason


def test_core_error_fixture_skipped_with_code(rig):
    p, issues = rig
    t = resolve_target(p, issues, "output:out3")
    assert t.fixtures == ()
    assert [i for i, _ in t.skipped] == list(idx(p, "fx11", "fx12"))
    assert all("ошибка ядра" in r and "channel_overlap" in r for _, r in t.skipped)


def test_skipped_beside_outputtable(rig):
    p, issues = rig
    t = resolve_target(p, issues, "fx10, fx1, fx11")
    assert t.fixtures == idx(p, "fx1")
    assert [i for i, _ in t.skipped] == list(idx(p, "fx10", "fx11"))


def test_synthetic_error_issue_marks_fixture(rig):
    p, issues = rig
    extra = api.Issue(level=api.IssueLevel.ERROR, code=api.IssueCode.OVER_512, message="за 512", fixture_ids=("fx4",))
    t = resolve_target(p, list(issues) + [extra], "port:out1/p1")
    assert t.fixtures == idx(p, "fx1", "fx2", "fx3")
    ((i, reason),) = t.skipped
    assert i == idx(p, "fx4")[0] and "over_512" in reason


def test_warning_issue_does_not_skip(rig):
    p, issues = rig
    warn = api.Issue(level=api.IssueLevel.WARNING, code=api.IssueCode.STALE_AUTO, message="w", fixture_ids=("fx1",))
    assert resolve_target(p, list(issues) + [warn], "fx1").fixtures == (0,)


def test_unsupported_shape_skipped(rig):
    p, issues = rig
    arc = api.ArcShape(center=(50.0, 50.0), radius=20.0, start_deg=0.0, end_deg=90.0)
    p2 = api.update_fixture(p, "fx1", shape=arc)
    t = resolve_target(p2, issues, "fx1, fx2")
    assert t.fixtures == idx(p2, "fx2")
    ((i, reason),) = t.skipped
    assert i == 0 and "форма" in reason


# --- 5.3, 5.4: порядок обхода ---------------------------------------------------------------

def test_walk_order_skips_not_output(rig):
    p, issues = rig
    assert walk_order(p, issues, False) == list(idx(p, *[f"fx{i}" for i in range(1, 10)]))


def test_walk_order_only_unchecked(rig):
    p, issues = rig
    p = api.set_acceptance(p, "fx2", Acceptance.OK)
    p = api.set_acceptance(p, "fx5", Acceptance.DEAD)
    p = api.set_acceptance(p, "fx10", Acceptance.NONE)
    p = api.set_acceptance(p, "fx9", Acceptance.WRONG_ADDRESS)
    assert walk_order(p, issues, True) == list(idx(p, "fx1", "fx3", "fx4", "fx6", "fx7", "fx8"))
    assert walk_order(p, issues, False) == list(idx(p, *[f"fx{i}" for i in range(1, 10)]))


def test_walk_order_all_checked_is_empty(rig):
    p, issues = rig
    for f in p.fixtures:
        p = api.set_acceptance(p, f.id, Acceptance.OK)
    assert walk_order(p, issues, True) == []


def test_walk_order_on_examples():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "examples"
    for path in sorted(root.glob("*.json")):
        p, _ = api.load(path)
        p, _ = api.auto_assign_all(p)
        assert walk_order(p, api.validate(p), False) == list(range(len(p.fixtures))), path.name


# --- 5.1, 5.2, 5.5: шаг ---------------------------------------------------------------------

ORDER = [0, 1, 2, 3, 4, 5, 6, 7, 8]
GAPPY = [1, 4, 7]


def test_step_forward_back():
    assert step(ORDER, 3, 1, True) == 4
    assert step(ORDER, 3, -1, True) == 2


def test_step_loop_both_ways():
    assert step(ORDER, 8, 1, True) == 0
    assert step(ORDER, 0, -1, True) == 8


def test_step_stop_both_ways():
    assert step(ORDER, 8, 1, False) == 8
    assert step(ORDER, 0, -1, False) == 0


def test_step_start_without_target():
    assert step(ORDER, None, 1, True) == 0
    assert step(ORDER, None, -1, False) == 0
    assert step(GAPPY, None, -1, True) == 1


def test_step_empty_order():
    assert step([], None, 1, True) is None
    assert step([], 3, -1, False) is None


def test_step_from_fixture_outside_order():
    assert step(GAPPY, 2, 1, False) == 4
    assert step(GAPPY, 2, -1, False) == 1
    assert step(GAPPY, 5, 1, True) == 7
    assert step(GAPPY, 5, -1, True) == 4


def test_step_outside_order_at_edges():
    assert step(GAPPY, 8, 1, True) == 1
    assert step(GAPPY, 8, 1, False) == 7
    assert step(GAPPY, 0, -1, True) == 7
    assert step(GAPPY, 0, -1, False) == 1


def test_step_single():
    assert step([5], 5, 1, True) == 5
    assert step([5], 5, -1, False) == 5


def test_full_walk_loops_through_rig(rig):
    p, issues = rig
    order = walk_order(p, issues, False)
    cur, seen = None, []
    for _ in range(len(order) + 1):
        cur = step(order, cur, 1, True)
        seen.append(cur)
    assert seen == order + [order[0]]


def test_walk_from_skipped_target(rig):
    p, issues = rig
    order = walk_order(p, issues, False)
    fx10 = idx(p, "fx10")[0]                          # не выводится — следующей выводимой нет после неё
    assert step(order, fx10, 1, True) == order[0]
    assert step(order, fx10, -1, True) == order[-1]


def _engine_fixtures(p, issues):
    """Индексы фикстур, которые движок кладёт в геометрию (колонка fx таблицы примитивов build_tables)."""
    import dsm_geometry
    t = dsm_geometry.build_tables(p, issues, api.segments_by_fixture(p), api.routing_table(p), 60.0)
    col = t.prims_rows[0].index("fx")
    return {int(r[col]) for r in t.prims_rows[1:]}


def test_not_output_rule_matches_engine_on_rig():
    """Locate подсвечивает ровно то, что выводит движок: правило «не выводится» записано в двух местах."""
    p, issues = _rig()
    assert set(walk_order(p, issues, False)) == _engine_fixtures(p, issues)


EXAMPLES = __import__("pathlib").Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.parametrize("name", sorted(f.name for f in EXAMPLES.glob("*.json")))
def test_not_output_rule_matches_engine_on_examples(name):
    p, _ = api.load(EXAMPLES / name)
    p, _ = api.auto_assign_all(p)
    issues = api.validate(p)
    assert set(walk_order(p, issues, False)) == _engine_fixtures(p, issues)
