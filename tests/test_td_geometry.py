"""pixel-engine 10.5: чистый модуль геометрии слоя TD тестируется без TouchDesigner.

Фабрики форм — здесь; патчи — из conftest и examples/. Содержательные тесты — задачи 2.1–2.3.
"""
import ast
import inspect
import pathlib

import pytest

from dsm_core import api
from conftest import fixture, rgb_type
from dsm_core.cells import cell_order, matrix_order
from dsm_core.model import Canvas, Direction, FixtureKind, FixtureType, LineShape, MatrixLayout, PolylineShape, RectShape

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
