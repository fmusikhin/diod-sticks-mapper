"""pixel-engine 10.5: чистый модуль геометрии слоя TD тестируется без TouchDesigner.

Фабрики форм — здесь; патчи — из conftest и examples/. Содержательные тесты — задачи 2.1–2.3.
"""
import ast
import inspect
import pathlib
from dataclasses import replace

import pytest

from dsm_core import api
from conftest import fixture, make_patch, output, rgb_type
from dsm_core.cells import cell_order, matrix_order
from dsm_core.model import (Address, AddressMode, Canvas, Channel, ChannelBlock, CurveShape, Direction, FixtureKind, FixtureType, LineShape,
                            MatrixLayout, PolylineShape, RectShape, Sampling, TargetKind)
from dsm_core.protocol import Protocol

import dsm_geometry

ROOT = pathlib.Path(__file__).resolve().parents[1]
TD_SRC = ROOT / "src" / "td"


# --- фабрики форм ---------------------------------------------------------------------------

def line_shape(x1=0.0, y1=0.0, x2=100.0, y2=0.0):
    return LineShape(p1=(x1, y1), p2=(x2, y2))


def polyline_shape(*points):
    return PolylineShape(points=tuple(points))


def rect_shape(cx=50.0, cy=50.0, w=40.0, h=20.0, rot=0.0):
    return RectShape(center=(cx, cy), size=(w, h), rotation_deg=rot)


@pytest.fixture(params=sorted((ROOT / "examples").glob("*.json")), ids=lambda p: p.stem)
def example_patch(request):
    return api.load(request.param)[0]


# --- 1.2: обвязка ----------------------------------------------------------------------------

def test_geometry_module_has_no_td_import():
    tree = ast.parse((TD_SRC / "dsm_geometry.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for n in names:
            assert n.split(".")[0] not in {"td", "numpy"}, f"dsm_geometry импортирует {n}"


def test_geotables_defaults_are_empty():
    t = dsm_geometry.GeoTables()
    assert t.cells_rows == [] and t.prims_rows == [] and t.routing_by_group == {} and t.warnings == ()


def test_signatures_match_design():
    expected = {
        "cell_positions": ["shape", "n", "direction", "cell_offset"],
        "rect_grid": ["shape", "width", "height"],
        "to_px": ["canvas", "xy"],
        "build_tables": ["patch", "issues", "segments", "routing", "fps"],
    }
    for name, params in expected.items():
        assert list(inspect.signature(getattr(dsm_geometry, name)).parameters) == params


def test_shape_factories_match_core_types():
    assert line_shape().kind == "line"
    assert polyline_shape((0, 0), (1, 0), (1, 1)).kind == "polyline"
    assert rect_shape(rot=90).kind == "rect"
    assert Canvas().width == 1280


def test_examples_load_for_geometry(example_patch):
    assert example_patch.fixtures


# --- 2.1: позиции ячеек ---------------------------------------------------------------------

def _close(a, b, eps=1e-6):
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def test_line_10_cells_centered_at_half_steps():
    pos = dsm_geometry.cell_positions(line_shape(0, 0, 100, 0), 10, Direction.FORWARD, 0)
    assert len(pos) == 10
    for i, p in enumerate(pos):
        assert _close(p, ((i + 0.5) * 10.0, 0.0))


def test_reverse_mirrors_data_index():
    fwd = dsm_geometry.cell_positions(line_shape(0, 0, 100, 0), 10, Direction.FORWARD, 0)
    rev = dsm_geometry.cell_positions(line_shape(0, 0, 100, 0), 10, Direction.REVERSE, 0)
    assert all(_close(rev[i], fwd[9 - i]) for i in range(10))


def test_cell_offset_rotates_positions():
    base = dsm_geometry.cell_positions(line_shape(0, 0, 100, 0), 10, Direction.FORWARD, 0)
    off = dsm_geometry.cell_positions(line_shape(0, 0, 100, 0), 10, Direction.FORWARD, 3)
    assert all(_close(off[i], base[(i + 3) % 10]) for i in range(10))


def test_polyline_uniform_by_arc_length():
    # отрезки 1 : 3 — 8 ячеек: 2 на первом (длина 10), 6 на втором (длина 30)
    shape = polyline_shape((0, 0), (10, 0), (10, 30))
    pos = dsm_geometry.cell_positions(shape, 8, Direction.FORWARD, 0)
    assert _close(pos[0], (2.5, 0.0)) and _close(pos[1], (7.5, 0.0))
    assert _close(pos[2], (10.0, 2.5)) and _close(pos[7], (10.0, 27.5))


def test_rect_as_strip_runs_along_width_with_rotation():
    pos = dsm_geometry.cell_positions(rect_shape(50, 50, 40, 10, rot=0), 4, Direction.FORWARD, 0)
    assert _close(pos[0], (35.0, 50.0)) and _close(pos[3], (65.0, 50.0))
    rot = dsm_geometry.cell_positions(rect_shape(50, 50, 40, 10, rot=90), 4, Direction.FORWARD, 0)
    assert _close(rot[0], (50.0, 35.0)) and _close(rot[3], (50.0, 65.0))


def test_rect_grid_rotation_90_places_cells():
    g = dsm_geometry.rect_grid(rect_shape(0, 0, 40, 20, rot=0), 4, 2)
    assert len(g) == 8
    assert _close(g[0], (-15.0, 5.0)) and _close(g[3], (15.0, 5.0)) and _close(g[4], (-15.0, -5.0))
    r = dsm_geometry.rect_grid(rect_shape(0, 0, 40, 20, rot=90), 4, 2)
    assert _close(r[0], (-5.0, -15.0)) and _close(r[3], (-5.0, 15.0))


@pytest.mark.parametrize("layout", list(MatrixLayout))
def test_matrix_all_16_layouts_match_core_order(layout):
    """Позиции по data_index = rect_grid[grid_index из cell_order]; раскладка видна по углу и ходу."""
    w, h = 4, 4
    ft = FixtureType(id="m", name="m", kind=FixtureKind.MATRIX, cells=16, width=w, height=h, layout=layout)
    fx = fixture("fx1", "m")
    grid = dsm_geometry.rect_grid(rect_shape(0, 0, 40, 40), w, h)
    pos = [grid[c.grid_index] for c in cell_order(ft, fx)]
    assert [grid[g] for g in matrix_order(w, h, layout)] == pos
    corner, line = layout.value.split("_")[0], layout.value.split("_")[1]
    first, second = pos[0], pos[1]
    assert (first[0] < 0) == (corner[1] == "l") and (first[1] > 0) == (corner[0] == "t")
    # первая линия идёт по строке (меняется x) или по столбцу (меняется y)
    assert (abs(second[0] - first[0]) > 1e-9) == (line == "rows")
    if layout.value.endswith("_snake"):
        assert abs(pos[w][0] - pos[w - 1][0]) < 1e-9 if line == "rows" else abs(pos[h][1] - pos[h - 1][1]) < 1e-9
    else:
        assert abs(pos[w][0] - pos[0][0]) < 1e-9 if line == "rows" else abs(pos[h][1] - pos[0][1]) < 1e-9


@pytest.mark.parametrize("direction", [Direction.FORWARD, Direction.REVERSE])
@pytest.mark.parametrize("offset", [0, 1, 5, 11])
def test_grid_for_data_matches_core_cell_order(direction, offset):
    fx = fixture("fx1", direction=direction, cell_offset=offset)
    core = [c.grid_index for c in cell_order(rgb_type(cells=12), fx)]
    assert dsm_geometry.grid_for_data(12, direction, offset) == core


def test_degenerate_shapes():
    assert dsm_geometry.cell_positions(line_shape(0, 0, 10, 0), 0, Direction.FORWARD, 0) == []
    zero = dsm_geometry.cell_positions(line_shape(5, 5, 5, 5), 3, Direction.FORWARD, 0)
    assert all(_close(p, (5.0, 5.0)) for p in zero)
    with pytest.raises(ValueError):
        dsm_geometry.cell_positions(polyline_shape((0, 0)), 3, Direction.FORWARD, 0)


def test_matrix_on_line_is_a_strip():
    pos = dsm_geometry.cell_positions(line_shape(0, 0, 160, 0), 16, Direction.FORWARD, 0)
    assert len(pos) == 16 and _close(pos[15], (155.0, 0.0))


def test_to_px_meters():
    c = Canvas(units="m", px_per_m=100.0)
    assert dsm_geometry.to_px(c, (1.5, 2.0)) == (150.0, 200.0)
    assert dsm_geometry.to_px(Canvas(), (3, 4)) == (3.0, 4.0)
    with pytest.raises(ValueError):
        dsm_geometry.to_px(Canvas(units="m"), (1, 1))


# --- 2.2–2.3: таблицы -----------------------------------------------------------------------

def _tables(p, fps=60.0):
    p, _ = api.auto_assign_all(p)
    issues = api.validate(p)
    return p, issues, dsm_geometry.build_tables(p, issues, api.segments_by_fixture(p), api.routing_table(p), fps)


def _col(rows, name):
    return rows[0].index(name)


def test_sticks_cells_and_prims_match_core():
    p, _ = api.load(ROOT / "examples" / "sticks_8x_artnet.json")
    p, issues, t = _tables(p)
    seg_rows = [r for r in api.segment_table(p)[1:] if r[4] == "cells"]
    n_cells = sum(int(r[6]) for r in seg_rows)
    assert len(t.cells_rows) - 1 == n_cells
    assert len(t.prims_rows) - 1 == len(seg_rows)
    cu, cc = _col(t.prims_rows, "DMXFixtureUniverse"), _col(t.prims_rows, "DMXFixtureChannel")
    assert [(r[cu], r[cc]) for r in t.prims_rows[1:]] == [(r[1], r[2]) for r in seg_rows]
    cell_col = _col(t.cells_rows, "cell")
    for prim, seg in zip(t.prims_rows[1:], seg_rows):
        first_vertex = int(prim[0].split()[0])
        assert t.cells_rows[1 + first_vertex][cell_col] == seg[5]       # vertices начинаются с first_cell
        assert len(prim[0].split()) == int(seg[6])
    assert len(t.arrows_rows) - 1 == len(p.fixtures)
    # UV в 0–1, одна фикстура = одна строка стрелки
    tu, tv = _col(t.cells_rows, "Tex(0)"), _col(t.cells_rows, "Tex(1)")
    assert all(0.0 <= float(r[tu]) <= 1.0 and 0.0 <= float(r[tv]) <= 1.0 for r in t.cells_rows[1:])


def test_error_fixtures_and_unassigned_are_excluded():
    fxs = [fixture("fx1"), fixture("fx2", x=10.0), fixture("fx3", x=20.0, address=Address())]
    p = make_patch([rgb_type(cells=10)], fxs, [output(fixture_ids=("fx1", "fx2"))])
    p = api.set_manual_address(p, "fx1", "out1", 0, 1)
    p = api.set_manual_address(p, "fx2", "out1", 0, 4)       # пересечение каналов с fx1
    issues = api.validate(p)
    assert any(i.code.value == "channel_overlap" for i in issues)
    t = dsm_geometry.build_tables(p, issues, api.segments_by_fixture(p), api.routing_table(p), 60.0)
    fx_col = _col(t.cells_rows, "fx")
    assert {r[fx_col] for r in t.cells_rows[1:]} == set()          # обе конфликтующие и unassigned исключены
    assert any("channel_overlap" in w for w in t.warnings) and any("fx3" in w for w in t.warnings)


def test_bar_head_tail_raw_rows():
    p, _ = api.load(ROOT / "examples" / "bar_common_channels.json")
    p, issues, t = _tables(p)
    segs = api.segment_table(p)[1:]
    raw_segs = [r for r in segs if r[4] in ("head", "tail")]
    assert len(t.raw_prims_rows) - 1 == len(raw_segs) > 0
    assert len(t.raw_rows) - 1 == sum(int(r[3]) - int(r[2]) + 1 for r in raw_segs)
    v = _col(t.raw_rows, "v")
    assert all(0.0 <= float(r[v]) <= 1.0 for r in t.raw_rows[1:])


def test_disabled_and_every_n_keep_address_but_inactive():
    fx = fixture("fx1", disabled=frozenset({2}), every_n=2)
    p, issues, t = _tables(make_patch([rgb_type(cells=6)], [fx], [output(fixture_ids=("fx1",))]))
    act = [r[_col(t.cells_rows, "active")] for r in t.cells_rows[1:]]
    assert act == ["1", "0", "0", "0", "1", "0"] and len(t.prims_rows[1][0].split()) == 6


def test_unsupported_shape_and_area_sampling():
    curve = replace(fixture("fx1"), shape=CurveShape(points=((0, 0), (1, 1), (2, 1), (3, 0))))
    area = fixture("fx2", x=10.0, sampling=Sampling(kind="area", size=3.0))
    p, issues, t = _tables(make_patch([rgb_type(cells=4)], [curve, area], [output(fixture_ids=("fx1", "fx2"))]))
    fx_col = _col(t.cells_rows, "fx")
    assert {r[fx_col] for r in t.cells_rows[1:]} == {"1"}
    assert any("curve" in w for w in t.warnings) and any("area" in w for w in t.warnings)


def test_matrix_on_line_is_strip_in_tables():
    ft = FixtureType(id="m", name="m", kind=FixtureKind.MATRIX, cells=16, width=4, height=4)
    fx = replace(fixture("fx1", "m"), shape=line_shape(0, 0, 160, 0))
    p, issues, t = _tables(make_patch([ft], [fx], [output(fixture_ids=("fx1",))]))
    xs = [float(r[0]) for r in t.cells_rows[1:]]
    assert xs == sorted(xs) and len(xs) == 16


def test_out_groups_delay_and_routing():
    outs = [output("out1", start=20, ip="127.0.0.1", fixture_ids=("fx1",)),
            output("out2", start=40, ip="127.0.0.2", fixture_ids=("fx2",)),
            output("out3", start=60, ip="127.0.0.3", fixture_ids=("fx3",), art_sync=True, delay_ms=50.0),
            output("out4", start=80, fixture_ids=("fx4",), delay_ms=50.0)]
    fxs = [fixture(f"fx{i}", output_id=f"out{i}", x=float(i)) for i in range(1, 5)]
    p, issues, t = _tables(make_patch([rgb_type(cells=10)], fxs, outs))
    assert [r[5] for r in t.out_groups[1:]] == ["out1 out2 out4", "out3"]       # art_sync делит, delay — нет
    assert t.delay_groups[1:] == [["0", "0", "0.0"], ["1", "3", "50.0"]]
    dg = _col(t.cells_rows, "dgrp"); fxc = _col(t.cells_rows, "fx")
    assert {r[fxc] for r in t.cells_rows[1:] if r[dg] == "1"} == {"2", "3"}
    g0 = t.routing_by_group["g0"]
    assert g0[1][:4] == ["0", "1", "4", "127.0.0.1"]                              # юниверс 20 Art-Net = 0.1.4
    assert any(r[3] == "127.0.0.1" and r[2] == "0" and r[1] == "5" for r in g0[1:])   # out4 без IP → 127.0.0.1 (80 = 0.5.0)
    assert any("target_missing" in w and "out4" in w and "127.0.0.1" in w for w in t.warnings)


def test_broadcast_without_ip_goes_to_255():
    out = output("out1", start=0, target_kind=TargetKind.BROADCAST, fixture_ids=("fx1",))
    p, issues, t = _tables(make_patch([rgb_type(cells=10)], [fixture("fx1")], [out]))
    assert t.routing_by_group["g0"][1][3] == "255.255.255.255"
    ip = _col(t.prims_rows, "DMXFixtureNetAddress(0)")
    assert t.prims_rows[1][ip:ip + 4] == ["255", "255", "255", "255"]
    assert not any("target_missing" in w for w in t.warnings)


def test_head_16bit_gives_high_low_bytes():
    head = ChannelBlock(channels=(Channel(name="dim", default=255, bits=16), Channel(name="strobe", default=8)))
    ft = rgb_type(cells=4, head=head)
    p, issues, t = _tables(make_patch([ft], [fixture("fx1")], [output(fixture_ids=("fx1",))]))
    v = _col(t.raw_rows, "v")
    assert [r[v] for r in t.raw_rows[1:]] == ["1.0", "0.0", repr(round(8 / 255, 6))]
    assert len(t.raw_prims_rows[1][0].split()) == 3 and t.raw_prims_rows[1][_col(t.raw_prims_rows, "DMXFixtureChannel")] == "1"
    assert t.prims_rows[1][_col(t.prims_rows, "DMXFixtureChannel")] == "4"


def test_sacn_multicast_routing_with_priority():
    p, _ = api.load(ROOT / "examples" / "matrix_16x16_sacn.json")
    p, issues, t = _tables(p)
    assert t.out_groups[1][1:3] == ["sacn", "1"]
    rows = t.routing_by_group["g0"][1:]
    assert rows[0][:4] == ["0", "0", "1", "239.255.0.1"] and rows[0][4] == str(p.outputs[0].priority)
    assert not any("127.0.0.1" in w for w in t.warnings)
    ip = _col(t.prims_rows, "DMXFixtureNetAddress(0)")
    assert t.prims_rows[1][ip:ip + 4] == ["239", "255", "0", "1"]
