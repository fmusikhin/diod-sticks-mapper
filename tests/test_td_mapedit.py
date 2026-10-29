"""Редактор маппинга: чистая логика холста (src/td/dsm_mapedit.py)."""
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
