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


# --- 2.2: строки таблицы на фикстуру (4.2, 4.4) ----------------------------------------------

def _engine_ncells(p, issues):
    """fx → число строк geo_cells движка (столько точек на фикстуру кладёт геометрия)."""
    import dsm_geometry
    t = dsm_geometry.build_tables(p, issues, api.segments_by_fixture(p), api.routing_table(p), 60.0)
    col = t.cells_rows[0].index("fx")
    res: dict[int, int] = {}
    for r in t.cells_rows[1:]:
        res[int(r[col])] = res.get(int(r[col]), 0) + 1
    return res


def _check_rows_against_core(p, issues, rows):
    segs = api.segments_by_fixture(p)
    bad = dsm_locate.not_output_reasons(p, issues, segs)
    engine = _engine_ncells(p, issues)
    types = {t.id: t for t in p.fixture_types}
    assert [r.fx for r in rows] == list(range(len(p.fixtures)))
    offset = 0
    for k, (fx, r) in enumerate(zip(p.fixtures, rows)):
        assert r.number == k + 1
        assert r.offset == offset
        if k in bad:
            assert (r.uni, r.ch, r.ncells) == (0, 0, 0)
            assert k not in engine
        else:
            first = segs[fx.id][0]
            assert (r.uni, r.ch) == (first.universe, first.first_channel)
            assert r.ncells == engine[k] == len(api.cell_order(types[fx.type_id], fx))
        offset += r.ncells


def test_fx_row_columns_order():
    r = dsm_locate.FxRow(fx=3, target=1, number=4, uni=12, ch=433, ncells=144, offset=432)
    assert r.as_row() == ["3", "1", "4", "12", "433", "144", "432"]
    assert len(r.as_row()) == len(dsm_locate.FX_COLUMNS)


def test_fixture_rows_rig_match_core_and_engine(rig):
    p, issues = rig
    rows = dsm_locate.fixture_rows(p, api.segments_by_fixture(p), None, issues)
    assert len(rows) == len(p.fixtures)
    assert all(r.target == 0 for r in rows)
    _check_rows_against_core(p, issues, rows)
    fx10, fx11, fx12 = idx(p, "fx10", "fx11", "fx12")
    for k in (fx10, fx11, fx12):
        assert (rows[k].uni, rows[k].ch, rows[k].ncells) == (0, 0, 0)
    assert rows[fx11].offset == rows[fx10].offset == 9 * 144     # сумма ячеек предыдущих выводимых


@pytest.mark.parametrize("name", ["strip_ws2812b.json", "sticks_8x_artnet.json"])
def test_fixture_rows_examples_match_core_and_engine(name):
    p, _ = api.load(EXAMPLES / name)
    p, _ = api.auto_assign_all(p)
    issues = api.validate(p)
    rows = dsm_locate.fixture_rows(p, api.segments_by_fixture(p), None, issues)
    assert len(rows) == len(p.fixtures) > 0
    _check_rows_against_core(p, issues, rows)
    assert sum(r.ncells for r in rows) == sum(_engine_ncells(p, issues).values())


def test_fixture_rows_target_flag_and_own_number(rig):
    """4.4: цель — группа; флаг цели у её выводимых фикстур, у каждой свой номер."""
    p, issues = rig
    t = resolve_target(p, issues, "group:g1")
    rows = dsm_locate.fixture_rows(p, api.segments_by_fixture(p), t, issues)
    flagged = [r.fx for r in rows if r.target == 1]
    assert flagged == list(t.fixtures) == list(idx(p, "fx2", "fx5"))
    assert [rows[k].number for k in flagged] == [k + 1 for k in flagged]
    assert rows[idx(p, "fx10")[0]].target == 0                    # в группе, но не выводится


def test_fixture_rows_default_issues_skips_only_address_and_shape(rig):
    """Без issues правило «не выводится» — только по адресу и форме (ошибки ядра не учтены)."""
    p, _ = rig
    rows = dsm_locate.fixture_rows(p, api.segments_by_fixture(p), None)
    assert rows[idx(p, "fx10")[0]].ncells == 0
    assert rows[idx(p, "fx11")[0]].ncells == 144


def test_fixture_rows_universe_and_channel_of_start(rig):
    p, issues = rig
    rows = dsm_locate.fixture_rows(p, api.segments_by_fixture(p), None, issues)
    fx1, fx2, fx9 = idx(p, "fx1", "fx2", "fx9")
    assert (rows[fx1].uni, rows[fx1].ch) == (0, 1)
    assert (rows[fx2].uni, rows[fx2].ch) == (0, 433)              # 144 × 3 = 432 канала первого стика
    assert (rows[fx9].uni, rows[fx9].ch) == (20, 1)


# --- 2.2: первая и последняя ячейка по порядку данных (3.4) -----------------------------------

@pytest.mark.parametrize("direction,offset,first_grid,last_grid", [
    ("forward", 0, 0, 9), ("reverse", 0, 9, 0), ("forward", 3, 3, 2), ("reverse", 3, 6, 7)])
def test_data_order_first_cell_follows_direction_and_offset(direction, offset, first_grid, last_grid):
    """Контракт шейдера: ячейка cell = data_index; data_index 0 — первая по проводу, n-1 — последняя."""
    from dsm_core.model import Direction
    ft = rgb_type(cells=10)
    base = api.cell_order(ft, fixture("a"))
    assert [c.grid_index for c in base] == list(range(10))           # прямая: data_index = grid_index
    fx = replace(fixture("b"), direction=Direction(direction), cell_offset=offset)
    order = api.cell_order(ft, fx)
    assert [c.data_index for c in order] == list(range(10))
    assert (order[0].grid_index, order[-1].grid_index) == (first_grid, last_grid)


def test_engine_puts_cells_in_data_order_on_reversed_fixture():
    """В geo_cells фикстура идёт по data_index 0..n-1; у перевёрнутой со сдвигом первая строка — первая по проводу."""
    import dsm_geometry
    from dsm_core.model import Direction
    fxs = [fixture("fx1"), replace(fixture("fx2", x=10.0), direction=Direction.REVERSE, cell_offset=5)]
    p = make_patch([rgb_type(cells=20)], fxs, [output(fixture_ids=("fx1", "fx2"))])
    p, _ = api.auto_assign_all(p)
    issues = api.validate(p)
    t = dsm_geometry.build_tables(p, issues, api.segments_by_fixture(p), api.routing_table(p), 60.0)
    c_fx, c_cell, c_y = (t.cells_rows[0].index(n) for n in ("fx", "cell", "P(1)"))
    for k in (0, 1):
        assert [int(r[c_cell]) for r in t.cells_rows[1:] if r[c_fx] == str(k)] == list(range(20))
    ys = {k: [float(r[c_y]) for r in t.cells_rows[1:] if r[c_fx] == str(k)] for k in (0, 1)}
    grid = {c.data_index: c.grid_index for c in api.cell_order(p.fixture_types[0], p.fixtures[1])}
    assert (grid[0], grid[19]) == (14, 15)
    assert ys[1][0] == pytest.approx(ys[0][grid[0]])               # позиция первой по проводу = её место на форме
    assert ys[1][-1] == pytest.approx(ys[0][grid[19]])


# --- 2.2: раскладка номера и адреса (4.1, 4.2, 4.3) --------------------------------------------

def _lay(seq):
    """Короткая запись: "R3 . G1" → ячейки; R red, G green, B blue, W white, D dim, . off."""
    names = {"R": "red", "G": "green", "B": "blue", "W": "white", "D": "dim", ".": "off"}
    out: list[str] = []
    for tok in seq.split():
        out += [names[tok[0]]] * (int(tok[1:]) if len(tok) > 1 else 1)
    return out


def _pad(cells, n):
    return cells + ["off"] * (n - len(cells))


@pytest.mark.parametrize("number,expect", [
    (0, "D"), (7, "B7"), (10, "G1 . D"), (105, "R1 . D . B5"), (999, "R9 . G9 . B9"),
    (1234, "W1 . R2 . G3 . B4"), (20304, "W2 . D . R3 . D . B4")])
def test_number_layout_digits(number, expect):
    cells, fits = dsm_locate.number_layout(number, 40)
    assert fits is True
    assert cells == _pad(_lay(expect), 40)
    assert set(cells) <= {"red", "green", "blue", "white", "dim", "off"}


def test_number_layout_exact_fit():
    assert dsm_locate.number_layout(105, 9) == (_lay("R1 . D . B5"), True)
    assert dsm_locate.number_layout(7, 7) == (_lay("B7"), True)


@pytest.mark.parametrize("number,ncells,expect", [
    (105, 5, "R1 . D . ."), (105, 8, "R1 . D . . . . ."), (999, 9, "R9"), (999, 4, ". . . ."),
    (7, 3, ". . ."), (0, 0, "")])
def test_number_layout_does_not_fit_shows_whole_high_digits(number, ncells, expect):
    cells, fits = dsm_locate.number_layout(number, ncells)
    assert fits is False
    assert cells == _lay(expect)
    assert len(cells) == ncells


def test_number_layout_rejects_negative():
    with pytest.raises(ValueError):
        dsm_locate.number_layout(-1, 10)


@pytest.mark.parametrize("uni,ch,expect", [
    (0, 1, "D . . B1"), (12, 1, "G1 . B2 . . B1"), (0, 433, "D . . R4 . G3 . B3"),
    (12, 433, "G1 . B2 . . R4 . G3 . B3")])
def test_address_layout(uni, ch, expect):
    cells, fits = dsm_locate.address_layout(uni, ch, 30)
    assert fits is True
    assert cells == _pad(_lay(expect), 30)


def test_address_layout_does_not_fit():
    # юниверс 12, две погашенные и сотни канала 433 — 10 ячеек; десятки (ещё 4) в 13 не помещаются
    cells, fits = dsm_locate.address_layout(12, 433, 13)
    assert fits is False
    assert cells == _pad(_lay("G1 . B2 . . R4"), 13)
    cells, fits = dsm_locate.address_layout(12, 433, 3)
    assert fits is False and cells == _lay("G1 . .")


# --- 2.3: сырой адрес (7.1, 7.3, 7.4, 7.5) --------------------------------------------------

import csv as _csv
import inspect as _inspect
import io as _io
import pathlib as _pathlib

from dsm_core.model import TargetKind
from dsm_core.protocol import Protocol

import dsm_geometry

_ROOT = _pathlib.Path(__file__).resolve().parents[1]
_PTS_COLUMNS = ["P(0)", "P(1)", "P(2)", "Color(0)", "Color(1)", "Color(2)", "Color(3)"]
_RAW_PRIM_COLUMNS = ["vertices", "DMXFixtureUniverse", "DMXFixtureChannel", "DMXFixtureNetAddress(0)",
                     "DMXFixtureNetAddress(1)", "DMXFixtureNetAddress(2)", "DMXFixtureNetAddress(3)"]


def _targets_patch():
    """Выходы на все виды цели: Art-Net unicast без IP и с IP, broadcast без IP и с IP, sACN multicast без IP,
    sACN unicast с IP. По одной фикстуре на выход."""
    specs = [("u_none", Protocol.ARTNET, TargetKind.UNICAST, None, 0),
             ("u_ip", Protocol.ARTNET, TargetKind.UNICAST, "192.0.2.10", 3),
             ("b_none", Protocol.ARTNET, TargetKind.BROADCAST, None, 300),
             ("b_ip", Protocol.ARTNET, TargetKind.BROADCAST, "192.0.2.255", 7),
             ("m_none", Protocol.SACN, TargetKind.MULTICAST, None, 1),
             ("s_ip", Protocol.SACN, TargetKind.UNICAST, "192.0.2.20", 600)]
    outs = [output(oid, protocol=pr, start=start, ip=ip, target_kind=tk, fixture_ids=(f"fx_{oid}",))
            for oid, pr, tk, ip, start in specs]
    fxs = [fixture(f"fx_{oid}", output_id=oid, x=float(k * 10)) for k, (oid, *_r) in enumerate(specs)]
    p = make_patch([rgb_type()], fxs, outs)
    p, _ = api.auto_assign_all(p)
    return p


def test_raw_rows_shape_and_values(rig):
    p, _ = rig
    r = dsm_locate.raw_rows(p, "out1", 0, 10, 20, 128)
    assert isinstance(r, dsm_locate.RawRows)
    assert r.pts_rows[0] == _PTS_COLUMNS
    assert len(r.pts_rows) == 1 + 512
    for k, row in enumerate(r.pts_rows[1:]):
        ch = k + 1
        assert len(row) == len(_PTS_COLUMNS)
        assert float(row[0]) == k and float(row[1]) == 0 and float(row[2]) == 0
        assert float(row[3]) == pytest.approx(128 / 255 if 10 <= ch <= 20 else 0.0)
        assert [float(v) for v in row[4:]] == [0.0, 0.0, 0.0]
    assert r.prims_rows[0] == _RAW_PRIM_COLUMNS
    assert len(r.prims_rows) == 2
    prim = r.prims_rows[1]
    assert prim[0].split() == [str(k) for k in range(512)]
    assert prim[1] == "0" and prim[2] == "1"
    assert prim[3:] == ["127", "0", "0", "1"]          # unicast без IP — 127.0.0.1


@pytest.mark.parametrize("first,last,value", [(1, 512, 255), (1, 1, 0), (512, 512, 255), (7, 7, 1)])
def test_raw_rows_bounds_accepted(rig, first, last, value):
    r = dsm_locate.raw_rows(rig[0], "out1", 0, first, last, value)
    on = [k + 1 for k, row in enumerate(r.pts_rows[1:]) if float(row[3]) > 0]
    assert on == ([] if value == 0 else list(range(first, last + 1)))
    assert all(float(row[3]) == pytest.approx(value / 255) for row in r.pts_rows[first:last + 1])


@pytest.mark.parametrize("out_id,universe,first,last,value,word", [
    ("out1", 0, 0, 10, 255, "канал"), ("out1", 0, 1, 513, 255, "канал"), ("out1", 0, 20, 10, 255, "канал"),
    ("out1", 0, -1, 10, 255, "канал"), ("out1", 0, 1, 10, -1, "значение"), ("out1", 0, 1, 10, 256, "значение"),
    ("nope", 0, 1, 10, 255, "выход"), ("", 0, 1, 10, 255, "выход"), ("out1", -1, 1, 10, 255, "юниверс")])
def test_raw_rows_rejects(rig, out_id, universe, first, last, value, word):
    with pytest.raises(dsm_locate.RawError) as e:
        dsm_locate.raw_rows(rig[0], out_id, universe, first, last, value)
    assert isinstance(e.value, ValueError)
    assert e.value.reason and word in e.value.reason


@pytest.mark.parametrize("bad", [1.5, "3", None, True])
def test_raw_rows_rejects_non_int(rig, bad):
    for args in ((bad, 1, 10, 255), (0, bad, 10, 255), (0, 1, bad, 255), (0, 1, 10, bad)):
        with pytest.raises(dsm_locate.RawError):
            dsm_locate.raw_rows(rig[0], "out1", *args)


def test_raw_rows_sacn_universe_out_of_protocol_range():
    p = _targets_patch()
    with pytest.raises(dsm_locate.RawError) as e:
        dsm_locate.raw_rows(p, "m_none", 0, 1, 512, 255)          # sACN: юниверс 1–63999
    assert "юниверс" in e.value.reason


def _out_unis(p, out_id):
    segs = api.segments_by_fixture(p)
    return {sg.universe for f in p.fixtures if f.address.output_id == out_id for sg in segs[f.id]}


def test_raw_rows_universe_not_in_patch_warns(rig):
    p, _ = rig
    free = max(_out_unis(p, "out1")) + 50
    r = dsm_locate.raw_rows(p, "out1", free, 1, 512, 255)
    assert r.warning and "не занят" in r.warning and str(free) in r.warning
    assert r.prims_rows[1][1] == str(free)           # шлётся всё равно, по цели выхода (7.3)
    assert dsm_locate.raw_rows(p, "out4", 60, 1, 512, 255).warning      # выход без фикстур


def test_raw_rows_universe_of_other_output_still_warns(rig):
    p, _ = rig
    u_out2 = api.segments_by_fixture(p)["fx9"][0].universe
    assert u_out2 not in _out_unis(p, "out1")
    assert dsm_locate.raw_rows(p, "out1", u_out2, 1, 512, 255).warning
    assert dsm_locate.raw_rows(p, "out2", u_out2, 1, 512, 255).warning == ""


def test_raw_rows_occupied_universe_no_warning(rig):
    p, _ = rig
    segs = api.segments_by_fixture(p)
    for f in p.fixtures:
        for sg in segs[f.id]:
            assert dsm_locate.raw_rows(p, f.address.output_id, sg.universe, 1, 512, 255).warning == ""


@pytest.mark.parametrize("out_id,expect_ip", [
    ("u_none", "127.0.0.1"), ("u_ip", "192.0.2.10"), ("b_none", "255.255.255.255"), ("b_ip", "192.0.2.255"),
    ("s_ip", "192.0.2.20")])
def test_raw_rows_target_ip_from_output_only(out_id, expect_ip):
    p = _targets_patch()
    for u in (300, 900, 5):                           # и вне патча — цель выхода (7.3, 7.5)
        prim = dsm_locate.raw_rows(p, out_id, u, 1, 512, 255).prims_rows[1]
        assert ".".join(prim[3:]) == expect_ip


def test_raw_rows_sacn_multicast_group():
    p = _targets_patch()
    prim = dsm_locate.raw_rows(p, "m_none", 0x0102, 1, 512, 255).prims_rows[1]
    assert prim[1] == str(0x0102) and ".".join(prim[3:]) == "239.255.1.2"


def test_raw_rows_has_no_ip_argument():
    assert list(_inspect.signature(dsm_locate.raw_rows).parameters) == [
        "patch", "output_id", "universe", "first", "last", "value"]


def _engine_prims(p):
    t = dsm_geometry.build_tables(p, api.validate(p), api.segments_by_fixture(p), api.routing_table(p), 60.0)
    cols = t.prims_rows[0]
    fi, ui, ai = cols.index("fx"), cols.index("DMXFixtureUniverse"), cols.index("DMXFixtureNetAddress(0)")
    return [(p.fixtures[int(r[fi])].address.output_id, r[ui], r[ai:ai + 4])
            for r in t.prims_rows[1:] + t.raw_prims_rows[1:]]


def _assert_raw_matches_engine(p):
    prims = _engine_prims(p)
    assert prims
    for out_id, uni, addr in prims:
        r = dsm_locate.raw_rows(p, out_id, int(uni), 1, 512, 255)
        assert r.warning == ""
        assert r.prims_rows[1][1] == uni
        assert r.prims_rows[1][3:7] == addr, (out_id, uni)


@pytest.mark.parametrize("path", sorted((_ROOT / "examples").glob("*.json")), ids=lambda q: q.stem)
def test_raw_rows_encoding_matches_engine_examples(path):
    _assert_raw_matches_engine(api.load(path)[0])


def test_raw_rows_encoding_matches_engine_rig(rig):
    _assert_raw_matches_engine(rig[0])


def test_raw_rows_encoding_matches_engine_all_target_kinds():
    _assert_raw_matches_engine(_targets_patch())


def test_locate_does_not_import_geometry():
    src = (_ROOT / "src" / "td" / "dsm_locate.py").read_text(encoding="utf-8")
    assert "import dsm_geometry" not in src and "from dsm_geometry" not in src


# --- 2.3: лист приёмки (8.3, 8.4) ------------------------------------------------------------

_SHEET_HEADER = ["n", "id", "name", "output", "universe", "channel", "status"]


def _rig_with_statuses():
    p, _ = _rig()
    for fid, st in [("fx1", Acceptance.OK), ("fx2", Acceptance.OK), ("fx3", Acceptance.REVERSED),
                    ("fx5", Acceptance.DEAD), ("fx7", Acceptance.MISPLACED), ("fx9", Acceptance.WRONG_ADDRESS)]:
        p = api.set_acceptance(p, fid, st)
    return p


def test_sheet_rows_rig():
    p = _rig_with_statuses()
    segs = api.segments_by_fixture(p)
    rows = dsm_locate.sheet_rows(p, segs)
    assert rows[0] == _SHEET_HEADER
    assert len(rows) == 1 + len(p.fixtures)
    for k, (f, row) in enumerate(zip(p.fixtures, rows[1:])):
        assert all(isinstance(c, str) for c in row)
        first = segs[f.id][0] if segs[f.id] else None
        assert row == [str(k + 1), f.id, f.name, f.address.output_id or "",
                       str(first.universe) if first else "", str(first.first_channel) if first else "",
                       f.acceptance.value]
    by_id = {r[1]: r for r in rows[1:]}
    assert by_id["fx3"][2] == "Стик, 3" and by_id["fx3"][6] == "reversed"
    assert by_id["fx10"][3:6] == ["", "", ""] and by_id["fx10"][6] == "none"
    assert by_id["fx9"][6] == "wrong_address" and by_id["fx4"][6] == "none"
    assert by_id["fx1"][4:6] == [str(segs["fx1"][0].universe), "1"]


def test_sheet_summary_rig():
    s = dsm_locate.sheet_summary(_rig_with_statuses())
    assert s == {"none": 6, "ok": 2, "reversed": 1, "dead": 1, "misplaced": 1, "wrong_address": 1, "unchecked": 6}


def test_sheet_summary_all_keys_with_zeros():
    p, _ = _rig()
    s = dsm_locate.sheet_summary(p)
    assert set(s) == {a.value for a in Acceptance} | {"unchecked"}
    assert s["none"] == s["unchecked"] == len(p.fixtures)
    assert all(s[a.value] == 0 for a in Acceptance if a != Acceptance.NONE)
    empty = make_patch([rgb_type()], [], [output()])
    assert dsm_locate.sheet_summary(empty) == {**{a.value: 0 for a in Acceptance}, "unchecked": 0}


def _read_back(text):
    return list(_csv.reader(_io.StringIO(text), delimiter=";"))


def test_sheet_csv_round_trip_rig():
    p = _rig_with_statuses()
    rows = dsm_locate.sheet_rows(p, api.segments_by_fixture(p))
    text = dsm_locate.sheet_csv(rows)
    assert isinstance(text, str)
    assert text.splitlines()[0] == ";".join(_SHEET_HEADER)
    assert _read_back(text) == rows
    assert _read_back(text.encode("utf-8").decode("utf-8")) == rows


def test_sheet_csv_round_trip_tricky_names():
    p, _ = _rig()
    names = ["Стик; с точкой с запятой", 'кавычки "внутри"', "1.5", "перевод\nстроки", "  пробелы  ", ""]
    fxs = list(p.fixtures)
    for k, nm in enumerate(names):
        fxs[k] = replace(fxs[k], name=nm)
    p = replace(p, fixtures=tuple(fxs))
    rows = dsm_locate.sheet_rows(p, api.segments_by_fixture(p))
    assert [r[2] for r in rows[1:1 + len(names)]] == names
    assert _read_back(dsm_locate.sheet_csv(rows)) == rows


def test_sheet_csv_uses_core_writer():
    from dsm_core.csv_io import CsvDialect, rows_to_csv
    rows = [["a", "b;c", "Ё"], ["1", "2.5", 'q"']]
    assert dsm_locate.sheet_csv(rows) == rows_to_csv(rows, CsvDialect(";", "."))
