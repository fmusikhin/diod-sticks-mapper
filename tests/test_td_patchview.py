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


# ---------- 1.6 чистый предпросмотр и сдвинувшиеся адреса (4.3, 4.8, 10.1) ----------

def _addr(patch):
    return {f.id: (f.address.output_id, f.address.universe, f.address.channel, f.address.mode) for f in patch.fixtures}


def _fx(patch, fid):
    return next(f for f in patch.fixtures if f.id == fid)


def test_preview_free_spot_matches_layout_fixture_and_rects_by_row():
    patch, layout = _layout()
    geom = pv.Geom()
    res = pv.preview(layout, patch, "fx1", "out1", 4, 1, geom)
    assert isinstance(res, pv.Preview) and res.error is None
    out1 = patch.outputs[0]
    ft = next(t for t in patch.fixture_types if t.id == "stick")
    assert list(res.segments) == api.layout_fixture(ft, _fx(patch, "fx1"), out1, 4, 1)
    assert res.segments[0].universe == 4 and res.segments[0].last_channel == 432
    assert res.issues == ()
    assert res.rects[0] == pv.RECT_COLUMNS
    body = res.rects[1:]
    n = len(layout.rows)
    r4 = _row(layout, "out1", 4)
    assert len(body) == 1
    assert body[0] == ([_f(0), _f(1 - (r4.index + 1) / n), _f(432 * CH), _f(1 / n)]
                       + [_f(c) for c in pv.fixture_color("fx1")] + [_f(0.6), "1", "cells", "fx1"])
    with pytest.raises(Exception):
        res.error = "x"                                                                  # frozen


def test_preview_over_other_fixture_reports_overlap_and_error_outline():
    patch, layout = _layout()
    geom = pv.Geom()
    res = pv.preview(layout, patch, "fx1", "out2", 10, 1, geom)
    assert res.error is None
    assert [(s.universe, s.first_channel, s.last_channel) for s in res.segments] == [(10, 1, 432)]
    codes = [(i.code, i.fixture_ids) for i in res.issues]
    assert (api.IssueCode.CHANNEL_OVERLAP, ("fx1", "fx5")) in codes
    assert (api.IssueCode.CHANNEL_OVERLAP, ("fx1", "fx6")) in codes
    # только касающиеся fx1: наложение fx5/fx6 между собой, port_limit fx3 и unassigned fx10 — нет
    assert all("fx1" in i.fixture_ids or (not i.fixture_ids and i.output_id == "out2" and i.universe == 10)
               for i in res.issues)
    assert not [i for i in res.issues if i.fixture_ids == ("fx5", "fx6")]
    body = res.rects[1:]
    n = len(layout.rows)
    r10 = _row(layout, "out2", 10)
    seg = [r for r in body if r[8] == "1"]
    issue = [r for r in body if r[8] == "2"]
    assert len(seg) == 1 and seg[0][1] == _f(1 - (r10.index + 1) / n) and seg[0][7] == _f(0.6)
    assert len(issue) == 1 and issue[0][:4] == seg[0][:4]
    assert issue[0][4:7] == [_f(c) for c in geom.error_color] and issue[0][9:] == ["issue", "fx1"]
    assert [int(r[8]) for r in body] == sorted(int(r[8]) for r in body)


def test_preview_impossible_address_is_error_not_exception():
    patch, layout = _layout()
    res = pv.preview(layout, patch, "fx1", "out1", 1, 600)
    assert res.error and "600" in res.error
    assert res.segments == () and res.issues == () and res.rects == []
    # auto_span выключен — стик 432 каналов с 400-го не помещается (LayoutError ядра)
    out1 = replace(patch.outputs[0], rules=replace(patch.outputs[0].rules, auto_span=False))
    p2 = replace(patch, outputs=(out1,) + patch.outputs[1:])
    res2 = pv.preview(layout, p2, "fx1", "out1", 1, 400)
    assert res2.error and "fx1" in res2.error and res2.segments == () and res2.rects == []
    # неизвестная фикстура / выход и юниверс вне диапазона протокола — тоже результат
    assert pv.preview(layout, patch, "nope", "out1", 1, 1).error
    assert pv.preview(layout, patch, "fx1", "nope", 1, 1).error
    assert pv.preview(layout, patch, "fx1", "out3", 0, 1).error                            # sACN от 1
    assert pv.preview(layout, patch, "fx1", "out1", 40000, 1).error                        # Art-Net до 32767


def test_preview_to_output_of_other_protocol():
    patch, layout = _layout()
    res = pv.preview(layout, patch, "fx1", "out3", 1, 1)                                   # Art-Net → sACN
    assert res.error is None
    assert [(s.universe, s.first_channel, s.last_channel) for s in res.segments] == [(1, 1, 432)]
    assert res.issues == ()                                                                # fx9 — auto, ядро сдвинуло его
    assert "fx9" in pv.shifted(patch, pv.apply_address(patch, "fx1", "out3", 1, 1))
    n = len(layout.rows)
    r3 = _row(layout, "out3", 1)
    assert [r[1] for r in res.rects[1:]] == [_f(1 - (r3.index + 1) / n)]
    # fx9 ручной — наложение
    pm = api.set_manual_address(patch, "fx9", "out3", 1, 1)
    res_m = pv.preview(layout, pm, "fx1", "out3", 1, 1)
    assert {i.code for i in res_m.issues} == {api.IssueCode.CHANNEL_OVERLAP}
    assert all("fx1" in i.fixture_ids for i in res_m.issues)
    assert [r[1] for r in res_m.rects[1:]] == [_f(1 - (r3.index + 1) / n)] * 2
    # по строкам раскладки только нужного выхода предпросмотр чужого выхода прямоугольников не даёт
    _, l2 = _layout(output_id="out2")
    res2 = pv.preview(l2, patch, "fx1", "out3", 1, 1)
    assert res2.segments and res2.rects == [pv.RECT_COLUMNS]


def test_preview_universe_outside_layout_rows_keeps_segments_without_rects():
    patch, layout = _layout()
    res = pv.preview(layout, patch, "fx1", "out3", 50, 1)
    assert res.error is None and [s.universe for s in res.segments] == [50]
    assert res.rects == [pv.RECT_COLUMNS]
    n = len(layout.rows)
    # стик fx5 с 433 юниверса 12 (out2 без universe_count) — хвост уйдёт в 13-й, которого нет в строках:
    # сегментов два, прямоугольник слоя 1 — один
    res2 = pv.preview(layout, patch, "fx5", "out2", 12, 433)
    assert res2.error is None and [s.universe for s in res2.segments] == [12, 13]
    seg = [r for r in res2.rects[1:] if r[8] == "1"]
    assert len(seg) == 1 and seg[0][10] == "fx5" and seg[0][1] == _f(1 - (_row(layout, "out2", 12).index + 1) / n)
    # у out1 заявлено 8 юниверсов — за 7-й ядро не пускает: это ошибка, не пустые сегменты
    res3 = pv.preview(layout, patch, "fx2", "out1", 7, 433)
    assert res3.error and res3.segments == () and res3.rects == []


def test_preview_multi_universe_segments_give_rect_per_row():
    patch, layout = _layout()
    res = pv.preview(layout, patch, "fx1", "out1", 5, 433)
    assert [(s.universe, s.first_channel, s.last_channel) for s in res.segments] == [(5, 433, 510), (6, 1, 354)]
    n = len(layout.rows)
    body = res.rects[1:]
    seg = [r for r in body if r[8] == "1"]
    assert [(r[0], r[1], r[2]) for r in seg] == [
        (_f(432 * CH), _f(1 - (_row(layout, "out1", 5).index + 1) / n), _f(78 * CH)),
        (_f(0), _f(1 - (_row(layout, "out1", 6).index + 1) / n), _f(354 * CH))]
    # ячейка на границе юниверса (ядро: cell_on_boundary для fx1) — жёлтая обводка на обоих сегментах
    assert {i.code for i in res.issues} == {api.IssueCode.CELL_ON_BOUNDARY}
    issue = [r for r in body if r[8] == "2"]
    assert [r[:4] for r in issue] == [r[:4] for r in seg]
    assert all(r[4:7] == [_f(c) for c in pv.Geom().warning_color] for r in issue)


def test_apply_address_and_shifted_track_auto_neighbours():
    patch, _ = _layout()
    after = pv.apply_address(patch, "fx2", "out1", 1, 1)
    a2 = _fx(after, "fx2").address
    assert (a2.mode, a2.output_id, a2.universe, a2.channel) == (api.AddressMode.MANUAL, "out1", 1, 1)
    assert api.auto_assign_all(after)[0] == after                                           # авто уже пересчитаны
    before_a, after_a = _addr(patch), _addr(after)
    expected = [f.id for f in after.fixtures if before_a[f.id] != after_a[f.id]]
    assert pv.shifted(patch, after) == expected
    assert expected[0] == "fx2" and len(expected) > 1                                        # соседи сдвинулись
    assert set(expected) == {"fx2", "fx3", "fx4"}
    # тот же адрес — ничего не сдвинулось
    same = pv.apply_address(patch, "fx5", "out2", 10, 1)
    assert pv.shifted(patch, same) == []
    assert pv.shifted(patch, patch) == []
    # ручной адрес, равный текущему авто: сам fx1 меняет режим → в списке, соседи нет
    manual_same = pv.apply_address(patch, "fx1", "out1", 0, 1)
    assert pv.shifted(patch, manual_same) == ["fx1"]
    # ошибки ядра из apply_address — исключение (правка, не предпросмотр)
    with pytest.raises(api.OperationError):
        pv.apply_address(patch, "fx1", "out1", 1, 600)


def test_shifted_includes_added_and_removed_fixtures_in_after_order():
    patch, _ = _layout()
    removed = api.remove_fixture(patch, "fx10")
    assert pv.shifted(patch, removed) == ["fx10"]
    assert pv.shifted(removed, patch) == ["fx10"]
    # добавленная + сдвинутая: порядок патча after
    after = pv.apply_address(removed, "fx2", "out1", 1, 1)
    assert pv.shifted(patch, after) == ["fx2", "fx3", "fx4", "fx10"]


def test_preview_and_apply_address_share_the_trial_patch():
    """Предпросмотр считается по тому же пробному патчу, что и реальная правка."""
    patch, layout = _layout()
    res = pv.preview(layout, patch, "fx2", "out1", 1, 1)
    after = pv.apply_address(patch, "fx2", "out1", 1, 1)
    assert list(res.segments) == api.fixture_segments(after, _fx(after, "fx2"))
    mine = [i for i in api.validate(after) if "fx2" in i.fixture_ids
            or (not i.fixture_ids and i.output_id == "out1" and i.universe in {s.universe for s in res.segments})]
    assert list(res.issues) == mine


# ---------- 1.7 чистые строки списков и лист адресов (3.3, 7.1, 10.1) ----------
import csv
import io


def test_issue_rows_follow_issue_table_errors_first():
    patch, _, issues = _load(PATCHVIEW_TEST)
    rows = pv.issue_rows(issues)
    assert rows[0] == ["n", "level", "code", "message", "fixture_ids", "output", "universe", "channels"]
    table = api.issue_table(patch)[1:]
    assert len(rows) - 1 == len(table) == 4
    assert [r[0] for r in rows[1:]] == ["1", "2", "3", "4"]
    assert [(r[1], r[2], r[3]) for r in rows[1:]] == [(t[0], t[1], t[2]) for t in table]
    levels = [r[1] for r in rows[1:]]
    assert levels == sorted(levels, key=lambda v: v != "error")                        # ошибки раньше предупреждений
    by_code = {r[2]: r for r in rows[1:]}
    assert by_code["channel_overlap"][4:] == ["fx5 fx6", "out2", "10", "400-432"]
    assert by_code["port_limit"][4:] == ["fx3", "out1", "", ""]
    assert by_code["unassigned"][4:] == ["fx10", "", "", ""]
    assert by_code["cell_on_boundary"][4:] == ["fx8", "out2", "11", ""]
    assert all(len(r) == len(rows[0]) for r in rows)
    assert pv.issue_rows([]) == [rows[0]]


def test_issue_rows_sorts_unsorted_input():
    _, _, issues = _load(PATCHVIEW_TEST)
    rows = pv.issue_rows(list(reversed(issues)))
    assert [r[1] for r in rows[1:]] == ["error", "error", "warning", "warning"]
    assert [r[0] for r in rows[1:]] == ["1", "2", "3", "4"]


def test_unassigned_rows_is_fx10():
    patch, segs, _ = _load(PATCHVIEW_TEST)
    rows = pv.unassigned_rows(patch, segs)
    fx10 = api.address_table(patch)[-1]
    assert rows == [["id", "name", "type", "cells"], ["fx10", fx10[1], fx10[2], "30"]]


def test_unassigned_rows_examples_empty_and_patch_order():
    for path in EXAMPLES:
        patch, segs, _ = _load(path)
        assert pv.unassigned_rows(patch, segs) == [["id", "name", "type", "cells"]]
    patch, segs, _ = _load(PATCHVIEW_TEST)
    segs = {k: ([] if k in ("fx7", "fx2") else v) for k, v in segs.items()}
    assert [r[0] for r in pv.unassigned_rows(patch, segs)[1:]] == ["fx2", "fx7", "fx10"]


OUTPUT_HEAD = ["id", "name", "protocol", "target", "ip", "start", "count", "used", "fixtures", "cells", "errors", "warnings"]


def test_output_rows_summaries():
    patch, segs, issues = _load(PATCHVIEW_TEST)
    rows = pv.output_rows(patch, segs, issues)
    assert rows[0] == OUTPUT_HEAD
    assert [r[0] for r in rows[1:]] == ["out1", "out2", "out3"]
    o1, o2, o3 = rows[1:]
    assert o1[1] == patch.outputs[0].name
    assert o1[2:] == ["artnet", "unicast", "127.0.0.1", "0", "8", "4", "4", str(144 * 3 + 30), "1", "0"]
    assert o2[2:] == ["artnet", "unicast", "127.0.0.2", "10", "", "3", "4", str(144 * 2 + 30 * 2), "1", "1"]
    assert o3[2:] == ["sacn", "unicast", "127.0.0.3", "1", "", "1", "1", "144", "0", "0"]
    for r, out in zip(rows[1:], patch.outputs):
        s = pv.output_summary(patch, segs, out.id)
        assert (r[7], r[8], r[9]) == (str(s["universes_used"]), str(s["fixtures"]), str(s["cells"]))


def test_output_rows_count_issue_by_fixture_without_output_id():
    patch, segs, issues = _load(PATCHVIEW_TEST)
    moved = [replace(i, output_id=None) if i.code == api.IssueCode.CHANNEL_OVERLAP else i for i in issues]
    rows = pv.output_rows(patch, segs, moved)
    assert rows[2][10:] == ["1", "1"]                                                   # по фикстурам fx5/fx6 выхода out2
    assert rows[1][10:] == ["1", "0"] and rows[3][10:] == ["0", "0"]


def test_output_rows_examples_one_row_per_output():
    for path in EXAMPLES:
        patch, segs, issues = _load(path)
        rows = pv.output_rows(patch, segs, issues)
        assert [r[0] for r in rows[1:]] == [o.id for o in patch.outputs]
        assert all(len(r) == len(OUTPUT_HEAD) for r in rows)
        assert sum(int(r[9]) for r in rows[1:]) == sum(s.n_cells for v in segs.values() for s in v)


def test_port_rows_chains():
    patch, _, _ = _load(PATCHVIEW_TEST)
    assert pv.port_rows(patch, "out1") == [
        ["port", "number", "new_universe", "max_cells", "max_universes", "chain"],
        ["port1", "1", "1", "", "", "fx1 fx2"],
        ["port2", "2", "1", "100", "", "fx3"]]
    assert pv.port_rows(patch, "out2") == [["port", "number", "new_universe", "max_cells", "max_universes", "chain"]]
    with pytest.raises(KeyError):
        pv.port_rows(patch, "nope")


def test_port_rows_examples():
    for path in EXAMPLES:
        patch, _, _ = _load(path)
        for out in patch.outputs:
            rows = pv.port_rows(patch, out.id)
            assert [r[0] for r in rows[1:]] == [p.id for p in out.ports]
            assert [r[5].split() for r in rows[1:]] == [list(p.fixture_ids) for p in out.ports]


def test_sheet_rows_is_address_table_and_filters_by_output():
    for path in ALL_PATCHES:
        patch, _, _ = _load(path)
        assert pv.sheet_rows(patch) == api.address_table(patch)
    patch, _, _ = _load(PATCHVIEW_TEST)
    full = api.address_table(patch)
    out2 = pv.sheet_rows(patch, output_id="out2")
    assert out2[0] == full[0]
    assert [r[0] for r in out2[1:]] == ["fx5", "fx6", "fx7", "fx8"]
    assert out2[1:] == [r for r in full[1:] if r[0] in {"fx5", "fx6", "fx7", "fx8"}]
    assert pv.sheet_rows(patch, output_id="nope") == [full[0]]


def test_sheet_csv_round_trip():
    patch, _, _ = _load(PATCHVIEW_TEST)
    rows = pv.sheet_rows(patch)
    rows = rows + [["x1", "1.5", "тип; с точкой", 'с "кавычками"', "artnet", "1", "0.0.1", "1", "3", "9", "1:1-9", "auto", "none"]]
    text = pv.sheet_csv(rows)
    assert isinstance(text, str)
    assert text.splitlines()[0] == ";".join(api.address_table(patch)[0])
    assert "1.5" in text and "1,5" not in text
    back = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert back == rows
    assert list(csv.reader(io.StringIO(text.encode("utf-8").decode("utf-8")), delimiter=";")) == rows   # UTF-8
    assert any(ord(ch) > 127 for r in back[1:-1] for c in r for ch in c)                 # кириллица имён патча
