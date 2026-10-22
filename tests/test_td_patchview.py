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


# ---------- 1.4 чистая раскладка карты (2.1, 2.2, 2.3, 2.4, 2.6, 2.7, 3.1, 10.1) ----------
import ast
import math
from dataclasses import replace

import pytest

import dsm_patchview as pv

EXAMPLES = sorted((ROOT / "examples").glob("*.json"))
ALL_PATCHES = EXAMPLES + [PATCHVIEW_TEST]
CH = 1 / 512


def _load(path):
    patch, _ = api.load(path)
    return patch, api.segments_by_fixture(patch), api.validate(patch)


def _layout(path=PATCHVIEW_TEST, output_id=None):
    patch, segs, issues = _load(path)
    return patch, pv.map_layout(patch, segs, issues, output_id)


def _row(layout, output_id, universe):
    return next(r for r in layout.rows if r.output_id == output_id and r.universe == universe)


def _f(v):
    return f"{v:.6f}"


def test_patchview_module_is_pure_and_does_not_import_geometry_or_locate():
    src = (ROOT / "src" / "td" / "dsm_patchview.py").read_text(encoding="utf-8")
    tree = ast.parse(src, feature_version=(3, 11))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not {m for m in mods if m.split(".")[0] in {"td", "dsm_geometry", "dsm_locate", "numpy"}}


def test_map_layout_rows_all_outputs_declared_range_and_order():
    patch, layout = _layout()
    rows = layout.rows
    assert [r.index for r in rows] == list(range(len(rows)))
    assert [(r.output_id, r.universe) for r in rows] == (
        [("out1", u) for u in range(8)] + [("out2", u) for u in (10, 11, 12)] + [("out3", 1)])
    assert [r.declared for r in rows if r.output_id == "out1"] == [True] * 8          # заявлено 0..7
    assert all(r.declared for r in rows if r.output_id in ("out2", "out3"))           # count не задан — от start
    assert _row(layout, "out1", 0).dotted == "0.0.0"
    assert _row(layout, "out1", 7).dotted == "0.0.7"
    assert _row(layout, "out2", 12).dotted == "0.0.12"
    assert _row(layout, "out3", 1).dotted == "1"                                      # sACN — сквозной номер
    assert _row(layout, "out1", 0).used_channels == 510                               # fx1 1–432 + fx2 433–510
    assert _row(layout, "out1", 1).used_channels == 354
    assert _row(layout, "out1", 3).used_channels == 12
    assert [_row(layout, "out1", u).used_channels for u in range(4, 8)] == [0] * 4
    assert _row(layout, "out2", 10).used_channels == 510                              # наложение считается один раз
    assert layout.skip_511_512 == {"out1": False, "out2": True, "out3": False}


def test_map_layout_single_output():
    _, layout = _layout(output_id="out2")
    assert [(r.output_id, r.universe, r.index) for r in layout.rows] == [("out2", 10, 0), ("out2", 11, 1), ("out2", 12, 2)]
    assert {s.fixture_id for s in layout.segments} == {"fx5", "fx6", "fx7", "fx8"}
    assert layout.skip_511_512 == {"out2": True}


def test_rows_beyond_declared_range_are_marked_undeclared():
    patch, segs, issues = _load(PATCHVIEW_TEST)
    out1 = replace(patch.outputs[0], universe_count=2)                                # заявлено 0..1, занято 0..3
    patch2 = replace(patch, outputs=(out1,) + patch.outputs[1:])
    layout = pv.map_layout(patch2, segs, issues, "out1")
    assert [(r.universe, r.declared) for r in layout.rows] == [(0, True), (1, True), (2, False), (3, False)]


def test_declared_range_beyond_artnet_max_still_builds_rows():
    """3.1: патч с universe_range (ошибка ядра) грузится — карта обязана строиться, а не падать на подписи."""
    patch, segs, issues = _load(PATCHVIEW_TEST)
    out1 = replace(patch.outputs[0], start_universe=32760, universe_count=10)
    patch2 = replace(patch, outputs=(out1,) + patch.outputs[1:])
    layout = pv.map_layout(patch2, segs, issues, "out1")
    unis = [r.universe for r in layout.rows]
    assert unis[:2] == [0, 1] and 32767 in unis and 32769 in unis          # занятые + заявленные за пределом
    beyond = next(r for r in layout.rows if r.universe == 32769)
    assert beyond.declared and beyond.dotted == "32769"
    assert next(r for r in layout.rows if r.universe == 32767).dotted == "127.15.15"


@pytest.mark.parametrize("path", ALL_PATCHES, ids=lambda p: p.stem)
def test_segments_match_core(path):
    patch, segs, issues = _load(path)
    layout = pv.map_layout(patch, segs, issues, None)
    by_fx = {}
    for s in layout.segments:
        row = layout.rows[s.row]
        assert row.output_id == next(f for f in patch.fixtures if f.id == s.fixture_id).address.output_id
        by_fx.setdefault(s.fixture_id, []).append((row.universe, s.first_channel, s.last_channel, s.kind, s.cells))
    expected = {fid: [(x.universe, x.first_channel, x.last_channel, x.kind, x.n_cells) for x in ss]
                for fid, ss in segs.items() if ss}
    assert by_fx == expected                                                          # фикстуры без сегментов — не в карте
    assert all(1 <= s.first_channel <= s.last_channel <= 512 for s in layout.segments)
    for s in layout.segments:
        assert s.color == pv.fixture_color(s.fixture_id)


def test_segment_kinds_head_cells_tail():
    _, layout = _layout(ROOT / "examples" / "bar_common_channels.json")
    fx1 = sorted((s.first_channel, s.kind) for s in layout.segments if s.fixture_id == "fx1")
    assert fx1 == [(1, "head"), (3, "cells"), (27, "tail")]


def test_issue_levels_from_validate():
    _, layout = _layout()
    lvl = {}
    for s in layout.segments:
        lvl.setdefault(s.fixture_id, set()).add(s.issue_level)
    assert lvl["fx5"] == {"error"} and lvl["fx6"] == {"error"}                          # channel_overlap
    assert lvl["fx3"] == {"error"}                                                      # port_limit
    assert lvl["fx8"] == {"warning"}                                                    # cell_on_boundary
    assert lvl["fx1"] == {""} and lvl["fx7"] == {""} and lvl["fx9"] == {""}


def test_issue_level_by_universe_and_channels_without_fixture_ids():
    patch, segs, _ = _load(PATCHVIEW_TEST)
    issue = api.Issue(api.IssueLevel.WARNING, api.IssueCode.UNIVERSE_RANGE, "x", (), "out2", None, 10, (495, 500))
    layout = pv.map_layout(patch, segs, [issue], "out2")
    lvl = {s.fixture_id: s.issue_level for s in layout.segments if layout.rows[s.row].universe == 10}
    assert lvl == {"fx5": "", "fx6": "", "fx7": "warning"}                               # задеты только каналы 490–510


def test_fixture_color_is_stable_and_distinct():
    assert pv.fixture_color("fx1") == pv.fixture_color("fx1")
    assert pv.fixture_color("fx1") != pv.fixture_color("fx2")
    for path in ALL_PATCHES:
        patch, _ = api.load(path)
        cols = [pv.fixture_color(f.id) for f in patch.fixtures]
        assert all(0.0 <= c <= 1.0 for col in cols for c in col)
        assert all(max(col) >= 0.5 for col in cols)                                     # не чёрные
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                assert math.dist(cols[i], cols[j]) > 0.15, (patch.fixtures[i].id, patch.fixtures[j].id)


def test_map_rects_background_segments_skip_issue_select():
    patch, layout = _layout()
    geom = pv.Geom()
    rows = pv.map_rects(layout, {"fx1"}, geom)
    assert rows[0] == ["x", "y", "w", "h", "r", "g", "b", "a", "layer", "kind", "id"]
    body = rows[1:]
    assert all(len(r) == 11 and all(isinstance(c, str) for c in r) for r in body)
    n = len(layout.rows)
    rh = 1 / n
    bg = [r for r in body if r[9] == "bg"]
    assert len(bg) == n and all(r[8] == "0" for r in bg)
    assert bg[0][:4] == [_f(0), _f(1 - rh), _f(1), _f(rh)]                              # строка 0 сверху
    assert bg[1][1] == _f(1 - 2 * rh)
    assert bg[0][4:7] == [_f(c) for c in geom.bg_declared]
    # недоступные 511–512 — только у out2 (3 строки)
    skip = [r for r in body if r[9] == "skip"]
    assert len(skip) == 3 and all(r[8] == "0" for r in skip)
    assert all(r[0] == _f(510 * CH) and r[2] == _f(2 * CH) for r in skip)
    r10 = _row(layout, "out2", 10)
    assert skip[0][1] == _f(1 - (r10.index + 1) * rh)
    # сегмент fx2 в юниверсе 0: каналы 433–510
    fx2 = [r for r in body if r[10] == "fx2" and r[8] == "1"]
    seg0 = next(r for r in fx2 if r[1] == _f(1 - rh))
    assert seg0[0] == _f(432 * CH) and seg0[2] == _f(78 * CH) and seg0[3] == _f(rh)
    assert seg0[4:8] == [_f(c) for c in pv.fixture_color("fx2")] + [_f(1.0)] and seg0[9] == "cells"
    # обводки проблем — слой 2: fx3/fx5/fx6 красные, fx8 жёлтые, у fx1 нет
    issue = [r for r in body if r[8] == "2"]
    assert all(r[9] == "issue" for r in issue)
    assert {r[10] for r in issue} == {"fx3", "fx5", "fx6", "fx8"}
    assert all(r[4:7] == [_f(c) for c in geom.error_color] for r in issue if r[10] in ("fx5", "fx6", "fx3"))
    assert all(r[4:7] == [_f(c) for c in geom.warning_color] for r in issue if r[10] == "fx8")
    # выделение — слой 3 только у fx1
    sel = [r for r in body if r[8] == "3"]
    assert sel and all(r[9] == "select" and r[10] == "fx1" for r in sel)
    assert len(sel) == 1 and sel[0][0] == _f(0) and sel[0][2] == _f(432 * CH)
    # порядок слоёв в таблице — по возрастанию
    assert [int(r[8]) for r in body] == sorted(int(r[8]) for r in body)


def test_map_rects_undeclared_background_color():
    patch, segs, issues = _load(PATCHVIEW_TEST)
    out1 = replace(patch.outputs[0], universe_count=2)
    layout = pv.map_layout(replace(patch, outputs=(out1,) + patch.outputs[1:]), segs, issues, "out1")
    geom = pv.Geom()
    bg = [r for r in pv.map_rects(layout, set(), geom)[1:] if r[9] == "bg"]
    assert [r[4:7] for r in bg] == [[_f(c) for c in geom.bg_declared]] * 2 + [[_f(c) for c in geom.bg_undeclared]] * 2


def test_map_rects_head_tail_alpha():
    _, layout = _layout(ROOT / "examples" / "bar_common_channels.json")
    body = pv.map_rects(layout, set(), pv.Geom())[1:]
    alphas = {r[9]: r[7] for r in body if r[8] == "1"}
    assert alphas["cells"] == _f(1.0) and alphas["head"] == _f(0.5) and alphas["tail"] == _f(0.5)
    assert not [r for r in body if r[8] == "3"]


def test_zoom_rects_grid_and_segments():
    patch, layout = _layout()
    geom = pv.Geom()
    row = _row(layout, "out2", 10)
    rows = pv.zoom_rects(layout, row.index, geom)
    assert rows[0] == ["x", "y", "w", "h", "r", "g", "b", "a", "layer", "kind", "id"]
    body = rows[1:]
    cells = [r for r in body if r[8] == "0"]
    assert len(cells) == 512
    assert [r[9] for r in cells].count("skip") == 2 and cells[510][9] == "skip"          # 511–512 недоступны
    cw, chh = 1 / 32, 1 / 16
    assert cells[0][:4] == [_f(0), _f(1 - chh), _f(cw), _f(chh)]                          # канал 1 — левый верх
    assert cells[31][:4] == [_f(31 * cw), _f(1 - chh), _f(cw), _f(chh)]
    assert cells[32][:4] == [_f(0), _f(1 - 2 * chh), _f(cw), _f(chh)]                     # канал 33 — вторая строка
    assert cells[511][:4] == [_f(31 * cw), _f(0), _f(cw), _f(chh)]
    segs = [r for r in body if r[8] == "1"]
    fx5 = [r for r in segs if r[10] == "fx5"]
    assert len(fx5) == 14                                                                 # 1–432: 13 полных строк + 16 клеток
    assert fx5[0][:4] == [_f(0), _f(1 - chh), _f(1), _f(chh)]
    assert fx5[13][:4] == [_f(0), _f(1 - 14 * chh), _f(16 * cw), _f(chh)]
    fx7 = [r for r in segs if r[10] == "fx7"]
    assert fx7 == [[_f(9 * cw), _f(0), _f(21 * cw), _f(chh)] + [_f(c) for c in pv.fixture_color("fx7")]
                   + [_f(1.0), "1", "cells", "fx7"]]                                      # 490–510 в последней строке
    assert {r[10] for r in body if r[8] == "2"} == {"fx5", "fx6"}
    assert not [r for r in body if r[8] == "3"]
    sel = [r for r in pv.zoom_rects(layout, row.index, geom, {"fx7"})[1:] if r[8] == "3"]
    assert len(sel) == 1 and sel[0][9:] == ["select", "fx7"]
    # строка без сегментов — только клетки
    empty = pv.zoom_rects(layout, _row(layout, "out1", 6).index, geom)[1:]
    assert len(empty) == 512 and all(r[9] == "cell" for r in empty)


def test_label_rows_universes_and_segments_with_wide_flag():
    patch, layout = _layout()
    geom = pv.Geom()
    rows = pv.label_rows(layout, geom)
    assert rows[0] == ["x", "y", "text", "kind", "id", "wide"]
    body = rows[1:]
    n = len(layout.rows)
    uni = [r for r in body if r[3] == "universe"]
    assert len(uni) == n
    assert uni[0][:3] == [_f(0), _f(1 - 0.5 / n), "0 (0.0.0)"] and uni[0][5] == "1"
    assert _row(layout, "out3", 1).index == n - 1 and uni[-1][2] == "1"
    segl = [r for r in body if r[3] == "segment"]
    assert len(segl) == len(layout.segments)
    fx1 = next(r for r in segl if r[4] == "fx1")
    assert fx1[:3] == [_f(0), _f(1 - 0.5 / n), "Стик 1 · 144 px"] and fx1[5] == "1"
    fx4 = [r for r in segl if r[4] == "fx4"]
    texts = {r[2]: r[5] for r in fx4}
    assert texts == {"Лента 4 · 26 px": "1", "Лента 4 · 4 px": "0"}                       # 12 каналов < 40/1280
    assert pv.Geom().label_min_frac == pytest.approx(40 / 1280)
    assert [r[5] for r in pv.label_rows(layout, pv.Geom(label_min_frac=0.0))[1:]] == ["1"] * len(body)


def test_label_rows_fall_back_to_fixture_id_without_name():
    patch, segs, issues = _load(PATCHVIEW_TEST)
    fx = [replace(f, name="") if f.id == "fx1" else f for f in patch.fixtures]
    layout = pv.map_layout(replace(patch, fixtures=tuple(fx)), segs, issues, "out1")
    texts = [r[2] for r in pv.label_rows(layout, pv.Geom())[1:] if r[4] == "fx1"]
    assert texts == ["fx1 · 144 px"]


def test_output_summary():
    patch, segs, _ = _load(PATCHVIEW_TEST)
    assert pv.output_summary(patch, segs, "out1") == {
        "universes_used": 4, "universes_declared": 8, "channels_used": 510 + 354 + 510 + 12,
        "fixtures": 4, "cells": 144 * 3 + 30}
    s2 = pv.output_summary(patch, segs, "out2")
    assert s2["universes_used"] == 3 and s2["universes_declared"] == 3 and s2["fixtures"] == 4
    assert s2["channels_used"] == 510 + 411 + 9 + 81 and s2["cells"] == 144 * 2 + 30 * 2
    assert pv.output_summary(patch, segs, "out3") == {
        "universes_used": 1, "universes_declared": 1, "channels_used": 432, "fixtures": 1, "cells": 144}


# ---------- 1.5 чистое попадание и адрес по точке (4.1, 4.2, 10.1) ----------

def _centers(rows_):
    """Центры всех прямоугольников таблицы pv_rects (без заголовка): (u, v, kind, id)."""
    for r in rows_:
        x, y, w, h = (float(c) for c in r[:4])
        yield x + w / 2, y + h / 2, r[9], r[10]


def test_hit_dataclass_is_frozen_and_none_outside_area():
    _, layout = _layout()
    for u, v in [(-0.01, 0.5), (1.01, 0.5), (0.5, -0.01), (0.5, 1.01)]:
        assert pv.hit(layout, u, v) == pv.Hit(None, None, None, None)
    with pytest.raises(Exception):
        pv.hit(layout, 0.5, 0.5).row = 3                                                 # frozen


def test_hit_and_map_rects_are_inverse_on_segment_centers():
    _, layout = _layout()
    rows = pv.map_rects(layout, set(), pv.Geom())
    n = len(layout.rows)
    seg_rows = [r for r in rows[1:] if r[8] == "1"]
    assert seg_rows
    for u, v, kind, fid in _centers(seg_rows):
        h = pv.hit(layout, u, v)
        assert h.segment is not None and h.segment.fixture_id == fid and h.fixture_id == fid
        assert h.segment.kind == kind
        assert h.row == h.segment.row and h.segment.first_channel <= h.channel <= h.segment.last_channel
        assert 1.0 - (h.row + 1) / n <= v < 1.0 - h.row / n
        assert (h.channel - 1) / 512 <= u < h.channel / 512


def test_hit_on_background_without_segment_gives_row_and_channel_only():
    _, layout = _layout()
    n = len(layout.rows)
    r6 = _row(layout, "out1", 6)                                                        # пустой заявленный юниверс
    v = 1.0 - (r6.index + 0.5) / n
    for k in (1, 100, 511, 512):
        assert pv.hit(layout, (k - 0.5) / 512, v) == pv.Hit(r6.index, k, None, None)
    # строка 0: fx1 1–432, fx2 433–510; 511–512 — никем
    h = pv.hit(layout, 511.5 / 512, 1.0 - 0.5 / n)
    assert h == pv.Hit(0, 512, None, None)


def test_hit_edges_and_clamping():
    _, layout = _layout()
    n = len(layout.rows)
    top = pv.hit(layout, 0.0, 1.0)                                                       # левый верх — строка 0, канал 1
    assert (top.row, top.channel, top.fixture_id) == (0, 1, "fx1")
    bottom = pv.hit(layout, 0.0, 0.0)                                                    # v = 0 — последняя строка
    assert (bottom.row, bottom.channel, bottom.fixture_id) == (n - 1, 1, "fx9")
    assert pv.hit(layout, 1.0, 0.5).channel == 512                                       # u = 1 — зажим в 512
    assert pv.hit(layout, 0.999999, 0.5).channel == 512
    assert pv.hit(layout, 1 / 512, 1.0).channel == 2                                     # граница канала — следующий
    assert pv.hit(layout, 0.0, 1.0 - 1 / n).row == 1                                     # граница строк — следующая
    empty = pv.MapLayout((), (), {})
    assert pv.hit(empty, 0.5, 0.5) == pv.Hit(None, None, None, None)


def test_hit_overlap_returns_last_drawn_segment():
    """fx5 (1–432) и fx6 (400–489) наложены в out2:10 — побеждает верхний (последний в layout.segments)."""
    _, layout = _layout()
    n = len(layout.rows)
    r10 = _row(layout, "out2", 10)
    v = 1.0 - (r10.index + 0.5) / n
    order = [s.fixture_id for s in layout.segments if s.row == r10.index]
    assert order.index("fx5") < order.index("fx6")
    h = pv.hit(layout, 419.5 / 512, v)
    assert h.channel == 420 and h.fixture_id == "fx6" and h.segment.first_channel == 400
    assert pv.hit(layout, 399.5 / 512, v).fixture_id == "fx6"                           # первый канал fx6
    assert pv.hit(layout, 398.5 / 512, v).fixture_id == "fx5"
    assert pv.hit(layout, 489.5 / 512, v).fixture_id == "fx7"                           # 490 — fx7


def test_zoom_hit_cells_and_inverse_with_zoom_rects():
    _, layout = _layout()
    geom = pv.Geom()
    row = _row(layout, "out2", 10).index
    cw, chh = 1 / 32, 1 / 16
    assert pv.zoom_hit(layout, row, 0.5 * cw, 1 - 0.5 * chh).channel == 1                # левый верх
    assert pv.zoom_hit(layout, row, 31.5 * cw, 1 - 0.5 * chh).channel == 32
    assert pv.zoom_hit(layout, row, 0.5 * cw, 1 - 1.5 * chh).channel == 33               # вторая строка сетки
    assert pv.zoom_hit(layout, row, 31.5 * cw, 0.5 * chh).channel == 512                 # правый низ
    assert pv.zoom_hit(layout, row, 1.0, 0.0).channel == 512                             # зажим краёв
    assert pv.zoom_hit(layout, row, 0.0, 1.0).channel == 1
    assert pv.zoom_hit(layout, row, 1.5, 0.5) == pv.Hit(None, None, None, None)
    h = pv.zoom_hit(layout, row, 9.5 * cw, 0.5 * chh)                                    # канал 490 — fx7
    assert (h.row, h.channel, h.fixture_id) == (row, 490, "fx7") and h.segment.fixture_id == "fx7"
    assert pv.zoom_hit(layout, row, 30.5 * cw, 0.5 * chh) == pv.Hit(row, 511, None, None)
    # взаимно обратны с zoom_rects: клетки слоя 0 → канал k по порядку; сегменты слоя 1 → та же фикстура
    rows = pv.zoom_rects(layout, row, geom)
    cells = [r for r in rows[1:] if r[8] == "0"]
    for k, (u, v, _, _) in enumerate(_centers(cells), start=1):
        assert pv.zoom_hit(layout, row, u, v).channel == k
    order = [s.fixture_id for s in layout.segments]
    for u, v, _, fid in _centers([r for r in rows[1:] if r[8] == "1"]):
        h = pv.zoom_hit(layout, row, u, v)
        # либо та же фикстура, либо её перекрыл сегмент, нарисованный позже (наложение fx5/fx6)
        assert h.fixture_id == fid or order.index(h.fixture_id) > order.index(fid)
    # fx5 (1–432) под fx6 (400–489): клетка 420 — fx6 (верхний)
    h420 = pv.zoom_hit(layout, row, 3.5 * cw, 1 - 13.5 * chh)
    assert (h420.channel, h420.fixture_id) == (420, "fx6")
    with pytest.raises(IndexError):
        pv.zoom_hit(layout, len(layout.rows), 0.5, 0.5)


def test_drop_address_snap_to_cell_grid_and_free():
    patch, layout = _layout()
    r1 = _row(layout, "out1", 1).index
    # стик GRB — 3 канала на ячейку: сетка 1, 4, 7, …
    assert pv.drop_address(layout, patch, "fx1", r1, 5, True) == ("out1", 1, 4)
    assert pv.drop_address(layout, patch, "fx1", r1, 6, True) == ("out1", 1, 7)
    assert pv.drop_address(layout, patch, "fx1", r1, 4, True) == ("out1", 1, 4)
    assert pv.drop_address(layout, patch, "fx1", r1, 1, True) == ("out1", 1, 1)
    assert pv.drop_address(layout, patch, "fx1", r1, 2, True) == ("out1", 1, 1)
    assert pv.drop_address(layout, patch, "fx1", r1, 3, True) == ("out1", 1, 4)
    assert pv.drop_address(layout, patch, "fx1", r1, 5, False) == ("out1", 1, 5)
    assert pv.drop_address(layout, patch, "fx1", r1, 6, False) == ("out1", 1, 6)
    # 16-битный тип — 6 каналов на ячейку: сетка 1, 7, 13
    ft16 = [replace(t, bits=16) if t.id == "strip30" else t for t in patch.fixture_types]
    p16 = replace(patch, fixture_types=tuple(ft16))
    assert pv.drop_address(layout, p16, "fx4", r1, 5, True) == ("out1", 1, 7)
    assert pv.drop_address(layout, p16, "fx4", r1, 3, True) == ("out1", 1, 1)


def test_drop_address_edges_other_output_and_bad_row():
    patch, layout = _layout()
    r1 = _row(layout, "out1", 1).index
    assert pv.drop_address(layout, patch, "fx1", r1, 0, False) == ("out1", 1, 1)
    assert pv.drop_address(layout, patch, "fx1", r1, -7, True) == ("out1", 1, 1)
    assert pv.drop_address(layout, patch, "fx1", r1, 600, False) == ("out1", 1, 512)
    assert pv.drop_address(layout, patch, "fx1", r1, 512, False) == ("out1", 1, 512)
    assert pv.drop_address(layout, patch, "fx1", r1, 600, True) == ("out1", 1, 511)     # ближайшее начало ячейки ≤ 512
    assert pv.drop_address(layout, patch, "fx1", r1, 511, True) == ("out1", 1, 511)
    # строка другого выхода — перенос (4.2): fx1 с out1 на строку out2:10
    r10 = _row(layout, "out2", 10).index
    assert pv.drop_address(layout, patch, "fx1", r10, 100, True) == ("out2", 10, 100)
    r3 = _row(layout, "out3", 1).index
    assert pv.drop_address(layout, patch, "fx5", r3, 7, False) == ("out3", 1, 7)
    for bad in (-1, len(layout.rows), 99):
        with pytest.raises(ValueError):
            pv.drop_address(layout, patch, "fx1", bad, 1, False)
    with pytest.raises(KeyError):
        pv.drop_address(layout, patch, "nope", r1, 1, False)


def test_drop_address_from_grab_keeps_grab_offset():
    patch, layout = _layout()
    n = len(layout.rows)
    r0 = _row(layout, "out1", 0)
    v0 = 1.0 - (r0.index + 0.5) / n
    grab = pv.hit(layout, 439.5 / 512, v0)                                              # fx2 433–510, захват на 440
    assert (grab.fixture_id, grab.channel) == ("fx2", 440)
    # отпустили на канале 100 строки 1 — начало сегмента 100 − 7 = 93; с привязкой → 94 (сетка …, 91, 94)
    r1 = _row(layout, "out1", 1)
    v1 = 1.0 - (r1.index + 0.5) / n
    assert pv.drop_address_from_grab(layout, patch, "fx2", grab, 99.5 / 512, v1, False) == ("out1", 1, 93)
    assert pv.drop_address_from_grab(layout, patch, "fx2", grab, 99.5 / 512, v1, True) == ("out1", 1, 94)
    # без сдвига курсора — прежний адрес
    assert pv.drop_address_from_grab(layout, patch, "fx2", grab, 439.5 / 512, v0, False) == ("out1", 0, 433)
    assert pv.drop_address_from_grab(layout, patch, "fx2", grab, 439.5 / 512, v0, True) == ("out1", 0, 433)
    # зажим в край: курсор левее смещения
    assert pv.drop_address_from_grab(layout, patch, "fx2", grab, 2.5 / 512, v1, False) == ("out1", 1, 1)
    # другой выход — перенос с сохранением смещения
    r10 = _row(layout, "out2", 10)
    v10 = 1.0 - (r10.index + 0.5) / n
    assert pv.drop_address_from_grab(layout, patch, "fx2", grab, 207.5 / 512, v10, False) == ("out2", 10, 201)
    # захват без сегмента (подложка) — смещение 0
    bare = pv.hit(layout, 0.5 / 512, 1.0 - (_row(layout, "out1", 6).index + 0.5) / n)
    assert bare.segment is None
    assert pv.drop_address_from_grab(layout, patch, "fx2", bare, 99.5 / 512, v1, False) == ("out1", 1, 100)
    # курсор вне области — ValueError
    with pytest.raises(ValueError):
        pv.drop_address_from_grab(layout, patch, "fx2", grab, 1.5, 0.5, False)


def test_drop_address_from_grab_offset_is_from_fixture_address_not_segment():
    """Смещение — от адреса фикстуры (первый сегмент), не от захваченного сегмента: head/tail и продолжение
    через границу юниверса. Отпускание на месте всегда возвращает текущий адрес."""
    # bar_common_channels fx1: адрес 0:1, head 1–2, cells 3–26, tail 27
    patch, layout = _layout(ROOT / "examples" / "bar_common_channels.json")
    n = len(layout.rows)
    v0 = 1.0 - 0.5 / n
    grab = pv.hit(layout, 9.5 / 512, v0)                                                # захват на канале 10 (cells)
    assert (grab.fixture_id, grab.channel, grab.segment.kind) == ("fx1", 10, "cells")
    assert pv.drop_address_from_grab(layout, patch, "fx1", grab, 9.5 / 512, v0, False) == ("out1", 0, 1)
    assert pv.drop_address_from_grab(layout, patch, "fx1", grab, 9.5 / 512, v0, True) == ("out1", 0, 1)
    tail = pv.hit(layout, 26.5 / 512, v0)                                               # захват за tail
    assert tail.segment.kind == "tail"
    assert pv.drop_address_from_grab(layout, patch, "fx1", tail, 26.5 / 512, v0, False) == ("out1", 0, 1)
    assert pv.drop_address_from_grab(layout, patch, "fx1", grab, 14.5 / 512, v0, False) == ("out1", 0, 6)   # сдвиг +5
    assert pv.drop_address_from_grab(layout, patch, "fx1", grab, 14.5 / 512, v0, True) == ("out1", 0, 7)    # сетка 1,4,7
    # стенд fx2: 0:433–510 + 1:1–354 — захват второго сегмента в строке юниверса 1
    patch, layout = _layout()
    n = len(layout.rows)
    r0, r1 = _row(layout, "out1", 0), _row(layout, "out1", 1)
    v0, v1 = 1.0 - (r0.index + 0.5) / n, 1.0 - (r1.index + 0.5) / n
    grab2 = pv.hit(layout, 99.5 / 512, v1)                                              # fx2, строка 1, канал 100
    assert (grab2.fixture_id, grab2.row, grab2.channel) == ("fx2", r1.index, 100)
    assert pv.drop_address_from_grab(layout, patch, "fx2", grab2, 99.5 / 512, v1, False) == ("out1", 0, 433)
    assert pv.drop_address_from_grab(layout, patch, "fx2", grab2, 99.5 / 512, v1, True) == ("out1", 0, 433)
    # сдвиг на N каналов сохраняет N; разница строк сохраняется (строка курсора 2 → адрес в строке 1)
    assert pv.drop_address_from_grab(layout, patch, "fx2", grab2, 109.5 / 512, v1, False) == ("out1", 0, 443)
    assert pv.drop_address_from_grab(layout, patch, "fx2", grab2, 89.5 / 512, v1, False) == ("out1", 0, 423)
    v2 = 1.0 - (_row(layout, "out1", 2).index + 0.5) / n
    assert pv.drop_address_from_grab(layout, patch, "fx2", grab2, 99.5 / 512, v2, False) == ("out1", 1, 433)
    # разница строк увела бы выше первой строки карты — зажим в строку 0
    assert pv.drop_address_from_grab(layout, patch, "fx2", grab2, 99.5 / 512, v0, False) == ("out1", 0, 433)
    # захват своего сегмента в строке адреса — как раньше
    grab0 = pv.hit(layout, 439.5 / 512, v0)
    assert pv.drop_address_from_grab(layout, patch, "fx2", grab0, 439.5 / 512, v0, False) == ("out1", 0, 433)
    # фикстуры нет в раскладке (не размещена) — смещение 0
    assert pv.drop_address_from_grab(layout, patch, "fx10", grab2, 99.5 / 512, v1, False) == ("out1", 1, 100)
