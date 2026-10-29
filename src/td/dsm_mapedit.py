"""Редактор маппинга (холст): попадание, перемещение, ручки линий, новые фикстуры, оверлей выделения.

Чистая логика без TouchDesigner (pytest). Координаты — пиксели холста патча: начало слева внизу, y вверх
(так же, как геометрия движка и выборка цвета: v = y / canvas.height).
"""
from __future__ import annotations

import math
from dataclasses import replace

from dsm_core.api import add_fixture, assign_to_output, next_ids, update_fixture
from dsm_core.model import (ArcShape, CurveShape, CustomShape, Direction, Fixture, LineShape, Patch, PolylineShape,
                            RectShape)

OVERLAY_COLUMNS = ["tx", "ty", "rz", "sx", "sy", "r", "g", "b", "a"]
SELECT_RGB = (1.0, 1.0, 1.0)
DRAG_RGB = (0.25, 0.75, 1.0)
HANDLE_PX = 9.0
LINE_PX = 2.0


def _rect_corners(s: RectShape) -> list[tuple[float, float]]:
    cx, cy = s.center
    hw, hh = s.size[0] / 2.0, s.size[1] / 2.0
    a = math.radians(s.rotation_deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]


def outline(shape) -> tuple[list[tuple[float, float]], bool]:
    """Точки контура фигуры для отрисовки и попадания; второй элемент — замкнут ли контур."""
    if isinstance(shape, LineShape):
        return [shape.p1, shape.p2], False
    if isinstance(shape, RectShape):
        return _rect_corners(shape), True
    if isinstance(shape, (PolylineShape, CurveShape, CustomShape)):
        return list(shape.points), False
    if isinstance(shape, ArcShape):
        n = 24
        a0, a1 = math.radians(shape.start_deg), math.radians(shape.end_deg)
        return [(shape.center[0] + shape.radius * math.cos(a0 + (a1 - a0) * k / n),
                 shape.center[1] + shape.radius * math.sin(a0 + (a1 - a0) * k / n)) for k in range(n + 1)], False
    return [], False


def _seg_dist(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _inside(p, poly) -> bool:
    x, y = p
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def hit(patch: Patch, x: float, y: float, tol: float, selected: tuple[str, ...] = ()) -> tuple[str | None, str | None]:
    """Что под точкой: (id фикстуры, ручка) — ручка ``p1``/``p2`` (концы линии выбранной фикстуры) или ``body``.
    Сверху — последняя в патче (рисуется позже). Ничего — ``(None, None)``."""
    p = (x, y)
    for fx in reversed(patch.fixtures):
        if fx.id in selected and isinstance(fx.shape, LineShape):
            for h in ("p1", "p2"):
                if math.hypot(x - getattr(fx.shape, h)[0], y - getattr(fx.shape, h)[1]) <= tol * 1.5:
                    return fx.id, h
    for fx in reversed(patch.fixtures):
        pts, closed = outline(fx.shape)
        if not pts:
            continue
        if closed and _inside(p, pts):
            return fx.id, "body"
        segs = list(zip(pts, pts[1:] + (pts[:1] if closed else [])))
        if len(pts) == 1 and math.hypot(x - pts[0][0], y - pts[0][1]) <= tol:
            return fx.id, "body"
        if any(_seg_dist(p, a, b) <= tol for a, b in segs):
            return fx.id, "body"
    return None, None


def shift_shape(shape, dx: float, dy: float):
    def mv(q):
        return (q[0] + dx, q[1] + dy)
    if isinstance(shape, LineShape):
        return replace(shape, p1=mv(shape.p1), p2=mv(shape.p2))
    if isinstance(shape, (PolylineShape, CurveShape, CustomShape)):
        return replace(shape, points=tuple(mv(q) for q in shape.points))
    if isinstance(shape, (RectShape, ArcShape)):
        return replace(shape, center=mv(shape.center))
    return shape


def snap(v: float, step: float) -> float:
    return round(v / step) * step if step > 0 else v


def drag_shape(fx: Fixture, handle: str, dx: float, dy: float, step: float = 0.0):
    """Фигура фикстуры после перетаскивания ручки на (dx, dy); ``step`` > 0 — привязка к сетке точки захвата."""
    s = fx.shape
    if handle in ("p1", "p2") and isinstance(s, LineShape):
        q = getattr(s, handle)
        return replace(s, **{handle: (snap(q[0] + dx, step), snap(q[1] + dy, step))})
    if step > 0:
        pts, _ = outline(s)
        if pts:
            ox, oy = pts[0]
            dx, dy = snap(ox + dx, step) - ox, snap(oy + dy, step) - oy
    return shift_shape(s, dx, dy)


def apply_drag(patch: Patch, ids, handle: str, dx: float, dy: float, step: float = 0.0) -> Patch:
    """Перетаскивание: ручка конца — только у первой фикстуры; тело — двигает все выбранные."""
    ids = list(ids)
    p = patch
    if handle in ("p1", "p2"):
        fx = next(f for f in p.fixtures if f.id == ids[0])
        return update_fixture(p, ids[0], shape=drag_shape(fx, handle, dx, dy, step))
    dx, dy = group_delta(patch, ids, dx, dy, step)
    for fid in ids:
        fx = next(f for f in p.fixtures if f.id == fid)
        p = update_fixture(p, fid, shape=shift_shape(fx.shape, dx, dy))
    return p


def group_delta(patch: Patch, ids, dx: float, dy: float, step: float) -> tuple[float, float]:
    """Сдвиг группы: к сетке прилипает первая (захваченная) фикстура, остальные сдвигаются на ту же величину —
    расстояния внутри группы сохраняются."""
    if step <= 0 or not ids:
        return dx, dy
    fx = next(f for f in patch.fixtures if f.id == list(ids)[0])
    pts, _ = outline(fx.shape)
    if not pts:
        return dx, dy
    ox, oy = pts[0]
    return snap(ox + dx, step) - ox, snap(oy + dy, step) - oy


def new_line(patch: Patch, type_id: str, p1, p2, output_id: str | None, name: str | None = None) -> tuple[Patch, str]:
    """Новая линейная фикстура профиля ``type_id`` от p1 к p2; на выходе ``output_id`` — в авто (адрес раздаст ядро)."""
    fid = next_ids(patch, "fixture")[0]
    ft = next(t for t in patch.fixture_types if t.id == type_id)
    n = sum(1 for f in patch.fixtures if f.type_id == type_id) + 1
    fx = Fixture(id=fid, name=name or f"{ft.name.split()[0]} {n}", type_id=type_id,
                 shape=LineShape(p1=tuple(map(float, p1)), p2=tuple(map(float, p2))))
    p = add_fixture(patch, fx)
    if output_id:
        p = assign_to_output(p, fid, output_id)
    return p, fid


def reverse(patch: Patch, ids) -> Patch:
    p = patch
    for fid in ids:
        fx = next(f for f in p.fixtures if f.id == fid)
        d = Direction.REVERSE if fx.direction == Direction.FORWARD else Direction.FORWARD
        p = update_fixture(p, fid, direction=d)
    return p


def _line_row(a, b, px: float, rgb, alpha: float = 1.0) -> list[str]:
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    return [f"{a[0]:.2f}", f"{a[1]:.2f}", f"{ang:.3f}", f"{max(L, 0.01):.2f}", f"{px:.2f}",
            f"{rgb[0]:g}", f"{rgb[1]:g}", f"{rgb[2]:g}", f"{alpha:g}"]


def overlay_rows(patch: Patch, selected, scale: float, drag=None, draw_new=None, bad=frozenset()) -> list[list[str]]:
    """Оверлей холста в пикселях холста: контуры выбранных (белые), ручки концов линий, призрак перетаскивания
    (``drag`` = (ids, handle, dx, dy, step)) и рисуемая линия (``draw_new`` = (p1, p2)). ``scale`` — пикселей холста
    на пиксель экрана (толщина и ручки постоянны на экране)."""
    rows = [list(OVERLAY_COLUMNS)]
    lw, hs = LINE_PX * scale, HANDLE_PX * scale
    sel = set(selected)
    for fx in patch.fixtures:                       # контуры всех: фикстура видна, даже если движок её не выводит
        if fx.id in sel:
            continue
        pts, closed = outline(fx.shape)
        rgb, alpha = ((1.0, 0.2, 0.2), 0.95) if fx.id in bad else ((0.7, 0.7, 0.75), 0.45)
        for a, b in zip(pts, pts[1:] + (pts[:1] if closed else [])):
            rows.append(_line_row(a, b, lw * (1.0 if fx.id in bad else 0.6), rgb, alpha))
    for fx in patch.fixtures:
        if fx.id not in sel:
            continue
        pts, closed = outline(fx.shape)
        for a, b in zip(pts, pts[1:] + (pts[:1] if closed else [])):
            rows.append(_line_row(a, b, lw, SELECT_RGB))
        if isinstance(fx.shape, LineShape):
            for q, rgb in ((fx.shape.p1, (0.3, 1.0, 0.4)), (fx.shape.p2, (1.0, 0.45, 0.3))):
                rows.append(_line_row((q[0] - hs / 2, q[1]), (q[0] + hs / 2, q[1]), hs, rgb))
    if drag is not None:
        ids, handle, dx, dy, step = drag
        targets = list(ids)[:1] if handle in ("p1", "p2") else list(ids)
        gdx, gdy = (dx, dy) if handle in ("p1", "p2") else group_delta(patch, ids, dx, dy, step)
        for fid in targets:
            fx = next((f for f in patch.fixtures if f.id == fid), None)
            if fx is None:
                continue
            shape = drag_shape(fx, handle, dx, dy, step) if handle in ("p1", "p2") else shift_shape(fx.shape, gdx, gdy)
            pts, closed = outline(shape)
            for a, b in zip(pts, pts[1:] + (pts[:1] if closed else [])):
                rows.append(_line_row(a, b, lw * 1.5, DRAG_RGB))
    if draw_new is not None:
        rows.append(_line_row(draw_new[0], draw_new[1], lw * 1.5, DRAG_RGB))
    return rows
