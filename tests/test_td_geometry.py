"""pixel-engine 10.5: чистый модуль геометрии слоя TD тестируется без TouchDesigner.

Фабрики форм — здесь; патчи — из conftest и examples/. Содержательные тесты — задачи 2.1–2.3.
"""
import ast
import inspect
import pathlib

import pytest

from dsm_core import api
from dsm_core.model import Canvas, LineShape, PolylineShape, RectShape

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
