"""Редактор маппинга: чистая логика холста (src/td/dsm_mapedit.py)."""
import math
import pathlib

from dsm_core import api

import dsm_mapedit as me

STAND = pathlib.Path(__file__).resolve().parents[1] / "td" / "stand" / "patchview_test.json"


def _patch():
    return api.load(str(STAND))[0]


def _fx(p, fid):
    return next(f for f in p.fixtures if f.id == fid)


def test_hit_line_body_and_handles():
    p = _patch()                                   # fx1: линия (120,100)–(120,620)
    assert me.hit(p, 121, 300, 5) == ("fx1", "body")
    assert me.hit(p, 120, 100, 5) == ("fx1", "body")              # без выбора ручек нет
    assert me.hit(p, 120, 100, 5, ("fx1",)) == ("fx1", "p1")
    assert me.hit(p, 121, 619, 5, ("fx1",)) == ("fx1", "p2")
    assert me.hit(p, 5, 5, 5) == (None, None)


def test_hit_rect_inside():
    p = _patch()                                   # fx4: прямоугольник центр (640,80) 300×20
    assert me.hit(p, 640, 82, 2) == ("fx4", "body")
    assert me.hit(p, 640, 120, 2) == (None, None)


def test_drag_body_moves_all_selected_and_handle_only_first():
    p = _patch()
    q = me.apply_drag(p, ["fx1", "fx4"], "body", 10, -5)
    assert _fx(q, "fx1").shape.p1 == (130.0, 95.0) and _fx(q, "fx4").shape.center == (650.0, 75.0)
    q = me.apply_drag(p, ["fx1", "fx4"], "p2", 0, 30)
    assert _fx(q, "fx1").shape.p2 == (120.0, 650.0) and _fx(q, "fx1").shape.p1 == (120.0, 100.0)
    assert _fx(q, "fx4").shape == _fx(p, "fx4").shape
    q = me.apply_drag(p, ["fx1"], "p2", 3, 33, step=10)
    assert _fx(q, "fx1").shape.p2 == (120.0, 650.0)


def test_new_line_auto_on_output_and_reverse():
    p = _patch()
    q, fid = me.new_line(p, "stick", (10, 10), (10, 500), "out1")
    fx = _fx(q, fid)
    assert fx.address.output_id == "out1" and fx.address.mode.value == "auto" and fx.shape.p2 == (10.0, 500.0)
    assert fid in q.outputs[0].fixture_ids
    r = me.reverse(q, [fid])
    assert _fx(r, fid).direction.value == "reverse"
    assert _fx(me.reverse(r, [fid]), fid).direction.value == "forward"


def test_overlay_rows_selection_drag_and_new():
    p = _patch()
    others = sum(len(me.outline(f.shape)[0]) - (0 if me.outline(f.shape)[1] else 1) for f in p.fixtures if f.id != "fx1")
    rows = me.overlay_rows(p, ("fx1",), 2.0, bad={"fx3"})
    assert rows[0] == me.OVERLAY_COLUMNS
    assert len(rows) == 1 + others + 1 + 2          # контуры остальных + контур линии + две ручки
    assert any(r[5:9] == ["1", "0.2", "0.2", "0.95"] for r in rows[1:])     # невыводимая fx3 — красная
    line = rows[1 + others]
    assert line[:5] == ["120.00", "100.00", "90.000", "520.00", "4.00"]
    others4 = sum(len(me.outline(f.shape)[0]) - (0 if me.outline(f.shape)[1] else 1) for f in p.fixtures if f.id != "fx4")
    rows = me.overlay_rows(p, ("fx4",), 1.0, drag=(("fx4",), "body", 5, 0, 0), draw_new=((0, 0), (10, 0)))
    assert len(rows) == 1 + others4 + 4 + 4 + 1


def test_group_drag_with_grid_keeps_spacing():
    p = _patch()
    q, ids = api.duplicate_fixtures(p, ["fx1"], 2, 0, 3)          # копии через 2 px: 122, 124, 126
    q = me.apply_drag(q, ["fx1", *ids], "body", 13, 0, step=10)    # fx1 120 → 130, остальные +10
    xs = [_fx(q, i).shape.p1[0] for i in ["fx1", *ids]]
    assert xs == [130.0, 132.0, 134.0, 136.0]


def test_view_fit_zoom_keeps_point_under_cursor():
    v = me.fit_view(1280, 720, 940, 529)
    x, y = me.to_canvas(v, 0.5, 0.5, 940, 529)
    assert abs(x - 640) < 1e-6 and abs(y - 360) < 1e-6
    z = me.zoom_at(v, 0.25, 0.75, 2.0, 940, 529)
    assert abs(z[2] - v[2] / 2) < 1e-9
    a, b = me.to_canvas(v, 0.25, 0.75, 940, 529), me.to_canvas(z, 0.25, 0.75, 940, 529)
    assert abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6


def test_box_select_touching():
    p = _patch()
    assert me.box_select(p, 100, 300, 130, 310) == ["fx1"]           # задевает середину палки
    assert set(me.box_select(p, 0, 0, 1280, 720)) == {f.id for f in p.fixtures}
    assert me.box_select(p, 1200, 0, 1210, 10) == []


def test_frame_scale_rotate_and_snap():
    p = _patch()
    bb = me.sel_bbox(p, ["fx1"])
    assert bb == (120.0, 100.0, 120.0, 620.0)
    assert me.hit_frame(bb, 120, 620, 1.0) in ("ne", "nw")          # нулевая ширина — углы совпадают
    piv, sx, sy = me.scale_params((0, 0, 100, 100), "ne", 200, 150, uniform=False, from_center=False)
    assert piv == (0, 0) and sx == 2.0 and sy == 1.5
    piv, sx, sy = me.scale_params((0, 0, 100, 100), "ne", 200, 150, uniform=True, from_center=False)
    assert sx == sy == 2.0
    q = me.apply_transform(p, ["fx1"], (120, 100), deg=90)
    s = _fx(q, "fx1").shape
    assert abs(s.p2[0] - (120 - 520)) < 1e-6 and abs(s.p2[1] - 100) < 1e-6
    piv, a = me.rotate_angle((0, 0, 100, 100), 50, 150, 150, 50, snap_deg=15)
    assert piv == (50.0, 50.0) and a == -90.0
    dx, dy, t = me.snap_delta([(0, 0)], [(10, 3)], 8, 2, 5)
    assert (dx, dy, t) == (10, 3, (10, 3))
    assert me.snap_delta([(0, 0)], [(50, 50)], 8, 2, 5) == (8, 2, None)


def test_renumber_copies():
    p = _patch()
    q, ids = api.duplicate_fixtures(p, ["fx1"], 10, 0, 3)
    q = me.renumber(q, ids)
    assert [_fx(q, i).name for i in ids] == ["Стик 10", "Стик 11", "Стик 12"]   # в примере есть «Стик 9»
    assert me._base_name("Стик 5 (1) (3)") == ("Стик", 5)



def test_tools_poly_rect_between():
    p = _patch()
    q, fid = me.new_fixture(p, "strip30", me.poly_shape([(0, 0), (100, 0), (100, 50)]), "out2")
    assert _fx(q, fid).shape.kind == "polyline" and _fx(q, fid).address.output_id == "out2"
    r = me.rect_shape((10, 10), (40, 30))
    assert r.center == (25.0, 20.0) and r.size == (30.0, 20.0)
    assert me.rect_shape((0, 0), (40, -10), square=True).size == (40.0, 40.0)
    q, ids = me.between(p, "fx1", "fx2", 3)                       # fx1 x=120, fx2 x=200
    xs = [_fx(q, i).shape.p1[0] for i in ids]
    assert xs == [140.0, 160.0, 180.0] and all(_fx(q, i).type_id == "stick" for i in ids)
    assert len(me.poly_rows([(0, 0), (10, 0)], (10, 10), 1.0)) == 2 + 2



def test_label_rows():
    p = _patch()
    rows = me.label_rows(p, 2.0, bad={"fx3"})
    assert rows[0] == me.LABEL_SPEC_COLUMNS and len(rows) == 1 + len(p.fixtures)
    r1 = next(r for r in rows[1:] if r[0].startswith("fx1 "))
    assert r1[0] == "fx1 · 0:1" and r1[1:4] == ["128.00", "92.00", "22.000"]
    assert next(r for r in rows[1:] if r[0].startswith("fx3 "))[4:6] == ["1", "0.45"]



def test_snap_angle_15():
    x, y = me.snap_angle((0, 0), (100, 30))                      # ~16.7° → 15°
    assert abs(math.degrees(math.atan2(y, x)) - 15) < 1e-9 and abs(math.hypot(x, y) - math.hypot(100, 30)) < 1e-9
    x, y = me.snap_angle((0, 0), (10, 100))                      # ~84° → 90°
    assert abs(x) < 1e-9



def test_matrix_type_line_rect_and_grid():
    p = _patch()
    q, tid = me.new_matrix_type(p, 144, 3, "tl_rows_snake")
    ft = next(t for t in q.fixture_types if t.id == tid)
    assert ft.kind.value == "matrix" and ft.cells == 432 and ft.width == 144 and me.is_matrix(q, tid)
    r = me.line_to_rect((0, 0), (0, 144), 144, 3)
    assert r.center == (0.0, 72.0) and abs(r.size[0] - 144) < 1e-9 and abs(r.size[1] - 3) < 1e-9 and r.rotation_deg == 90.0
    q, fid = me.new_fixture(q, tid, me.line_to_rect((100, 100), (100, 244), 144, 3), "out1")
    assert api.validate(q) is not None
    grid = me.matrix_rows(q, [fid], 1.0)
    assert len(grid) == 143 + 2 + 1 or len(grid) == 1          # сетка (если не слишком частая) + первая ячейка
    q2, t2 = me.new_matrix_type(p, 8, 8, "br_cols")
    q2, f2 = me.new_fixture(q2, t2, me.rect_shape((0, 0), (80, 80)), None)
    rows = me.matrix_rows(q2, [f2], 1.0)
    assert len(rows) == 7 + 7 + 1
    first = rows[-1]                                          # br_cols: старт — правый нижний угол → клетка (75, 5)
    assert abs(float(first[0]) + float(first[3]) / 2 - 75) < 1e-6 and abs(float(first[1]) - 5) < 1e-6



def test_output_view_roundtrip_keeps_input_shape():
    p = _patch()
    v = me.to_output_view(p)
    assert _fx(v, "fx1").shape == _fx(p, "fx1").shape                        # без output_shape — как вход
    v2 = me.apply_shift(v, ["fx1"], 50, 0)
    q = me.from_output_view(v2, p)
    assert _fx(q, "fx1").shape == _fx(p, "fx1").shape                        # вход (выборка) не тронут
    assert _fx(q, "fx1").output_shape.p1 == (170.0, 100.0)                   # выход сдвинут
    assert _fx(q, "fx2").output_shape is None                                # нетронутые — без output_shape
    v3 = me.to_output_view(q)
    assert _fx(v3, "fx1").shape.p1 == (170.0, 100.0)                         # вид снова берёт output_shape
    q2 = me.from_output_view(me.apply_shift(v3, ["fx1"], -50, 0), q)
    assert _fx(q2, "fx1").output_shape == _fx(p, "fx1").shape                # вернули на место — остаётся явным
    v4, fid = me.new_fixture(v, "stick", LineShapeT((0, 0), (0, 100)), "out1")
    q3 = me.from_output_view(v4, p)
    assert _fx(q3, fid).shape.p2 == (0.0, 100.0) and _fx(q3, fid).output_shape is None
    assert api.save is not None and len(api.validate(q3)) >= 0


def LineShapeT(a, b):
    from dsm_core.model import LineShape
    return LineShape(p1=a, p2=b)



def test_library_profile_new_update_and_shapes():
    p = _patch()
    q, tid = me.new_profile(p, 144, 1)
    ft = next(t for t in q.fixture_types if t.id == tid)
    assert ft.kind.value == "strip" and ft.cells == 144 and me.profile_dims(ft) == (144, 1)
    q, fid = me.new_fixture(q, tid, me.default_shape(q, tid, (640, 360)), "out1")
    assert _fx(q, fid).shape.kind == "line"
    q = me.update_profile(q, tid, 144, 3, "bl_cols_snake", "GRB")                 # стала 3 ряда — палка → прямоугольник
    ft = next(t for t in q.fixture_types if t.id == tid)
    assert ft.kind.value == "matrix" and ft.cells == 432 and ft.layout.value == "bl_cols_snake" and ft.color_order == "GRB"
    assert _fx(q, fid).shape.kind == "rect" and abs(_fx(q, fid).shape.size[1] - 30) < 1e-6
    q = me.update_profile(q, tid, 100, 1, "tl_rows", "RGB")
    ft = next(t for t in q.fixture_types if t.id == tid)
    assert ft.kind.value == "strip" and ft.cells == 100 and me.profile_summary(ft) == "100×1"
    assert me.default_shape(q, tid, (0, 0)).p1 == (-500.0, 0)



def test_input_edit_keeps_output_in_place():
    p = _patch()
    q = me.pin_outputs(me.apply_shift(p, ["fx1"], 30, 0), p)
    assert _fx(q, "fx1").shape.p1 == (150.0, 100.0)                          # вход сдвинут
    assert _fx(q, "fx1").output_shape == _fx(p, "fx1").shape                 # выход остался на месте
    assert _fx(q, "fx2").output_shape is None                                # нетронутые — без output_shape
    q2 = me.pin_outputs(me.apply_shift(q, ["fx1"], 10, 0), q)
    assert _fx(q2, "fx1").output_shape == _fx(p, "fx1").shape                # уже закреплённый — не меняется



def test_set_profile_converts_input_and_output():
    p = _patch()
    q, tid = me.new_matrix_type(p, 16, 2)
    v = me.apply_shift(me.to_output_view(q), ["fx1"], 300, 0)
    q = me.from_output_view(v, q)                               # выход разведён со входом
    r = me.set_profile(q, "fx1", tid)
    f = _fx(r, "fx1")
    assert f.type_id == tid and f.shape.kind == "rect" and f.output_shape.kind == "rect"
    assert abs(f.output_shape.center[0] - f.shape.center[0] - 300) < 1e-6


def test_pin_outputs_keeps_output_on_input_edit():
    p = _patch()
    q = me.pin_outputs(me.apply_shift(p, ["fx1"], 100, 0), p)
    assert _fx(q, "fx1").output_shape == _fx(p, "fx1").shape and _fx(q, "fx2").output_shape is None
