"""Редактор маппинга (холст): попадание, перемещение, ручки линий, новые фикстуры, оверлей выделения.

Чистая логика без TouchDesigner (pytest). Координаты — пиксели холста патча: начало слева внизу, y вверх
(так же, как геометрия движка и выборка цвета: v = y / canvas.height).
"""
from __future__ import annotations

import math
from dataclasses import replace

from dsm_core.api import add_fixture, add_fixture_type, assign_to_output, next_ids, update_fixture, update_fixture_type
from dsm_core.model import (ArcShape, CurveShape, CustomShape, Direction, Fixture, FixtureKind, FixtureType, LineShape,
                            MatrixLayout, Patch, PolylineShape, RectShape)

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


def overlay_rows(patch: Patch, selected, scale: float, drag=None, draw_new=None, bad=frozenset(),
                 colors=None) -> list[list[str]]:
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
        if fx.id in bad:
            rgb, alpha, k = (1.0, 0.2, 0.2), 0.95, 1.0
        elif colors and fx.id in colors:                 # OUTPUT: нет контента под фикстурами — цвет фикстуры, толще
            rgb, alpha, k = colors[fx.id], 0.95, 2.0
        else:
            rgb, alpha, k = (0.7, 0.7, 0.75), 0.45, 0.6
        for a, b in zip(pts, pts[1:] + (pts[:1] if closed else [])):
            rows.append(_line_row(a, b, lw * k, rgb, alpha))
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


# ---------- вид холста (зум, пан) ----------

def fit_view(cw: float, ch: float, aw: float, ah: float, margin: float = 0.04) -> tuple[float, float, float]:
    """Вид, в который целиком входит холст: (x, y левого нижнего угла, ширина вида) в пикселях холста."""
    vw = max(cw, ch * aw / ah) * (1 + 2 * margin)
    vh = vw * ah / aw
    return cw / 2 - vw / 2, ch / 2 - vh / 2, vw


def to_canvas(view, u: float, v: float, aw: float, ah: float) -> tuple[float, float]:
    vx, vy, vw = view
    return vx + u * vw, vy + v * vw * ah / aw


def zoom_at(view, u: float, v: float, factor: float, aw: float, ah: float,
            min_w: float = 20.0, max_w: float = 100000.0) -> tuple[float, float, float]:
    """Масштаб к курсору: точка холста под (u, v) остаётся под курсором; factor > 1 — приблизить."""
    x, y = to_canvas(view, u, v, aw, ah)
    vw = max(min_w, min(max_w, view[2] / factor))
    return x - u * vw, y - v * vw * ah / aw, vw


# ---------- выбор рамкой ----------

def _sample(pts, closed, n: int = 8):
    segs = list(zip(pts, pts[1:] + (pts[:1] if closed else [])))
    if not segs:
        return list(pts)
    return [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for a, b in segs for k in range(n + 1)]


def box_select(patch: Patch, x0: float, y0: float, x1: float, y1: float) -> list[str]:
    """Фикстуры, задетые рамкой (любая точка контура внутри) — как выделение рамкой в Resolume."""
    lx, hx, ly, hy = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
    out = []
    for fx in patch.fixtures:
        pts, closed = outline(fx.shape)
        segs = list(zip(pts, pts[1:] + (pts[:1] if closed else []))) or [(q, q) for q in pts]
        if any(_seg_in_box(a, b, lx, ly, hx, hy) for a, b in segs) or (closed and _inside(((lx + hx) / 2, (ly + hy) / 2), pts)):
            out.append(fx.id)
    return out


def _seg_in_box(a, b, lx, ly, hx, hy) -> bool:
    """Отрезок задевает прямоугольник (Лианг — Барски)."""
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - lx), (dx, hx - a[0]), (-dy, a[1] - ly), (dy, hy - a[1])):
        if p == 0:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                return False
    return True


# ---------- рамка трансформации ----------

def sel_bbox(patch: Patch, ids):
    pts = [q for f in patch.fixtures if f.id in set(ids) for q in outline(f.shape)[0]]
    if not pts:
        return None
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return min(xs), min(ys), max(xs), max(ys)


ROT_OFFSET_PX = 26.0


def frame_handles(bbox, scale: float) -> dict:
    """Ручки рамки: углы (масштаб) и ``rot`` над верхней серединой (поворот)."""
    x0, y0, x1, y1 = bbox
    return {"sw": (x0, y0), "se": (x1, y0), "ne": (x1, y1), "nw": (x0, y1),
            "rot": ((x0 + x1) / 2, y1 + ROT_OFFSET_PX * scale)}


def hit_frame(bbox, x: float, y: float, scale: float):
    tol = HANDLE_PX * scale
    for name, (hx, hy) in frame_handles(bbox, scale).items():
        if abs(x - hx) <= tol and abs(y - hy) <= tol:
            return name
    return None


OPPOSITE = {"sw": "ne", "ne": "sw", "se": "nw", "nw": "se"}


def scale_params(bbox, handle: str, x: float, y: float, uniform: bool, from_center: bool):
    """Опора и коэффициенты масштаба для ручки-угла, перетащенной в (x, y)."""
    hs = frame_handles(bbox, 1.0)
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    px, py = (cx, cy) if from_center else hs[OPPOSITE[handle]]
    hx, hy = hs[handle]
    sx = (x - px) / (hx - px) if abs(hx - px) > 1e-9 else 1.0
    sy = (y - py) / (hy - py) if abs(hy - py) > 1e-9 else 1.0
    if uniform:
        s = max(abs(sx), abs(sy))
        sx, sy = math.copysign(s, sx), math.copysign(s, sy)
    return (px, py), sx, sy


def rotate_angle(bbox, x0: float, y0: float, x: float, y: float, snap_deg: float = 0.0):
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    a = math.degrees(math.atan2(y - cy, x - cx) - math.atan2(y0 - cy, x0 - cx))
    a = (a + 180.0) % 360.0 - 180.0
    if snap_deg > 0:
        a = round(a / snap_deg) * snap_deg
    return (cx, cy), a


def _xf_point(q, pivot, sx, sy, deg):
    x, y = (q[0] - pivot[0]) * sx, (q[1] - pivot[1]) * sy
    a = math.radians(deg)
    return (pivot[0] + x * math.cos(a) - y * math.sin(a), pivot[1] + x * math.sin(a) + y * math.cos(a))


def transform_shape(shape, pivot, sx: float = 1.0, sy: float = 1.0, deg: float = 0.0):
    """Масштаб (sx, sy) и поворот (deg) фигуры вокруг pivot."""
    def f(q):
        return _xf_point(q, pivot, sx, sy, deg)
    if isinstance(shape, LineShape):
        return replace(shape, p1=f(shape.p1), p2=f(shape.p2))
    if isinstance(shape, (PolylineShape, CurveShape, CustomShape)):
        return replace(shape, points=tuple(f(q) for q in shape.points))
    if isinstance(shape, RectShape):
        return replace(shape, center=f(shape.center), size=(abs(shape.size[0] * sx), abs(shape.size[1] * sy)),
                       rotation_deg=shape.rotation_deg + deg)
    if isinstance(shape, ArcShape):
        return replace(shape, center=f(shape.center), radius=shape.radius * (abs(sx) + abs(sy)) / 2,
                       start_deg=shape.start_deg + deg, end_deg=shape.end_deg + deg)
    return shape


def apply_transform(patch: Patch, ids, pivot, sx: float = 1.0, sy: float = 1.0, deg: float = 0.0) -> Patch:
    p = patch
    for fid in ids:
        fx = next(f for f in p.fixtures if f.id == fid)
        p = update_fixture(p, fid, shape=transform_shape(fx.shape, pivot, sx, sy, deg))
    return p


def apply_shift(patch: Patch, ids, dx: float, dy: float) -> Patch:
    p = patch
    for fid in ids:
        fx = next(f for f in p.fixtures if f.id == fid)
        p = update_fixture(p, fid, shape=shift_shape(fx.shape, dx, dy))
    return p


# ---------- привязка к соседям ----------

def endpoints(patch: Patch, exclude=()) -> list:
    """Точки привязки: концы линий и углы прямоугольников невыбранных фикстур."""
    ex = set(exclude)
    return [q for f in patch.fixtures if f.id not in ex for q in outline(f.shape)[0]]


def snap_delta(moving, targets, dx: float, dy: float, tol: float):
    """Сдвиг (dx, dy) с привязкой: ближайшая пара «движущаяся точка → цель» в пределах tol прилипает.
    Возвращает (dx, dy, цель или None)."""
    best = None
    for mx, my in moving:
        qx, qy = mx + dx, my + dy
        for tx, ty in targets:
            d = math.hypot(qx - tx, qy - ty)
            if d <= tol and (best is None or d < best[0]):
                best = (d, tx - mx, ty - my, (tx, ty))
    if best is None:
        return dx, dy, None
    return best[1], best[2], best[3]


def frame_rows(bbox, scale: float, rgb=(0.95, 0.75, 0.2)) -> list:
    """Рамка выбора с ручками углов и поворота (строки оверлея)."""
    x0, y0, x1, y1 = bbox
    lw, hs = 1.0 * scale, HANDLE_PX * scale
    rows = [_line_row(a, b, lw, rgb, 0.9) for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                                                       ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0)))]
    hsx = frame_handles(bbox, scale)
    rows.append(_line_row(((x0 + x1) / 2, y1), hsx["rot"], lw, rgb, 0.9))
    for name, (hx, hy) in hsx.items():
        rows.append(_line_row((hx - hs / 2, hy), (hx + hs / 2, hy), hs, (1.0, 1.0, 1.0) if name == "rot" else rgb))
    return rows


def box_rows(x0, y0, x1, y1, scale: float) -> list:
    lw = 1.0 * scale
    c = (0.4, 0.8, 1.0)
    return [_line_row(a, b, lw, c, 0.9) for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                                                     ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0)))]


def mark_rows(q, scale: float, rgb=(1.0, 0.2, 0.9)) -> list:
    """Крестик-направляющая в точке привязки."""
    s = 10 * scale
    return [_line_row((q[0] - s, q[1]), (q[0] + s, q[1]), 1.5 * scale, rgb),
            _line_row((q[0], q[1] - s), (q[0], q[1] + s), 1.5 * scale, rgb)]


def _base_name(name: str) -> tuple[str, int | None]:
    """«Стик 5 (1) (3)» → («Стик», 5): убрать хвосты копий « (n)» и номер в конце."""
    import re
    s = re.sub(r"(\s*\(\d+\))+$", "", name).strip()
    m = re.match(r"^(.*?)[\s_-]*(\d+)$", s)
    return (m.group(1).strip() or s, int(m.group(2))) if m else (s, None)


def renumber(patch: Patch, ids) -> Patch:
    """Имена новых фикстур (копии массива/вставки): база имени + следующий свободный номер — «Стик 9», «Стик 10»."""
    used: dict[str, int] = {}
    new = set(ids)
    for f in patch.fixtures:
        if f.id in new:
            continue
        b, n = _base_name(f.name)
        used[b] = max(used.get(b, 0), n or 0)
    p = patch
    for fid in ids:
        fx = next(f for f in p.fixtures if f.id == fid)
        b, _n = _base_name(fx.name)
        used[b] = used.get(b, 0) + 1
        p = update_fixture(p, fid, name=f"{b} {used[b]}")
    return p



# ---------- инструменты: ломаная, прямоугольник, раскладка между двумя ----------

def new_fixture(patch: Patch, type_id: str, shape, output_id: str | None, name: str | None = None) -> tuple[Patch, str]:
    """Новая фикстура профиля ``type_id`` с фигурой ``shape``; на выходе ``output_id`` — в авто."""
    fid = next_ids(patch, "fixture")[0]
    ft = next(t for t in patch.fixture_types if t.id == type_id)
    n = sum(1 for f in patch.fixtures if f.type_id == type_id) + 1
    p = add_fixture(patch, Fixture(id=fid, name=name or f"{ft.name.split()[0]} {n}", type_id=type_id, shape=shape))
    if output_id:
        p = assign_to_output(p, fid, output_id)
    return p, fid


def poly_shape(points) -> PolylineShape:
    return PolylineShape(points=tuple((float(x), float(y)) for x, y in points))


def rect_shape(p0, p1, square: bool = False) -> RectShape:
    """Прямоугольник по двум углам; ``square`` — квадрат по большей стороне."""
    w, h = p1[0] - p0[0], p1[1] - p0[1]
    if square:
        s = max(abs(w), abs(h))
        w, h = math.copysign(s, w or 1), math.copysign(s, h or 1)
    return RectShape(center=(p0[0] + w / 2, p0[1] + h / 2), size=(abs(w), abs(h)), rotation_deg=0.0)


def between(patch: Patch, id_a: str, id_b: str, count: int, output_id: str | None = None) -> tuple[Patch, list[str]]:
    """N линий, равномерно разложенных между двумя линиями (концы интерполируются) — профиль, выход и направление
    первой; имена продолжают нумерацию."""
    a = next(f for f in patch.fixtures if f.id == id_a)
    b = next(f for f in patch.fixtures if f.id == id_b)
    if not (isinstance(a.shape, LineShape) and isinstance(b.shape, LineShape)):
        raise ValueError("раскладка между: обе фикстуры должны быть линиями")
    out = output_id if output_id is not None else a.address.output_id
    p, ids = patch, []
    for k in range(1, count + 1):
        s = k / (count + 1)
        lerp = lambda q0, q1: (q0[0] + (q1[0] - q0[0]) * s, q0[1] + (q1[1] - q0[1]) * s)  # noqa: E731
        p, fid = new_fixture(p, a.type_id, LineShape(p1=lerp(a.shape.p1, b.shape.p1), p2=lerp(a.shape.p2, b.shape.p2)),
                             out, name=a.name)
        if a.direction != Direction.FORWARD:
            p = update_fixture(p, fid, direction=a.direction)
        ids.append(fid)
    return renumber(p, ids), ids


def poly_rows(points, cursor, scale: float) -> list:
    """Рисуемая ломаная: отрезки по точкам, резинка до курсора, точки-ручки."""
    rows = []
    pts = list(points) + ([cursor] if cursor is not None else [])
    for a, b in zip(pts, pts[1:]):
        rows.append(_line_row(a, b, LINE_PX * 1.5 * scale, DRAG_RGB))
    hs = HANDLE_PX * 0.8 * scale
    for q in points:
        rows.append(_line_row((q[0] - hs / 2, q[1]), (q[0] + hs / 2, q[1]), hs, DRAG_RGB))
    return rows



# ---------- подписи на холсте ----------
LABEL_SPEC_COLUMNS = ["text", "tx", "ty", "fontsize", "fontcolorr", "fontcolorg", "fontcolorb", "fontalpha"]


def label_rows(patch: Patch, scale: float, px: float = 11.0, bad=frozenset()) -> list:
    """Geo Text (specdat): у начала каждой фикстуры «id · юниверс:канал» (размер постоянный на экране)."""
    rows = [list(LABEL_SPEC_COLUMNS)]
    fs = px * scale
    for fx in patch.fixtures:
        pts, _ = outline(fx.shape)
        if not pts:
            continue
        x, y = pts[0]
        a = fx.address
        addr = f"{a.universe}:{a.channel}" if a.universe is not None and a.channel is not None else "—"
        rgb = (1.0, 0.45, 0.45) if fx.id in bad else (1.0, 1.0, 1.0)
        rows.append([f"{fx.id} · {addr}", f"{x + 4 * scale:.2f}", f"{y - 4 * scale:.2f}", f"{fs:.3f}",
                     f"{rgb[0]:g}", f"{rgb[1]:g}", f"{rgb[2]:g}", "0.95"])
    return rows



def snap_angle(anchor, q, step_deg: float = 15.0):
    """Точка q, повёрнутая вокруг anchor к ближайшему углу, кратному step_deg (длина сохраняется) — Shift при рисовании."""
    dx, dy = q[0] - anchor[0], q[1] - anchor[1]
    L = math.hypot(dx, dy)
    if L == 0:
        return q
    a = math.radians(round(math.degrees(math.atan2(dy, dx)) / step_deg) * step_deg)
    return (anchor[0] + L * math.cos(a), anchor[1] + L * math.sin(a))



# ---------- матрицы W×H ----------

def new_matrix_type(patch: Patch, width: int, height: int, layout: str = "tl_rows", color_order: str = "RGB",
                    name: str | None = None) -> tuple[Patch, str]:
    """Новый матричный профиль W×H (W ячеек по ширине — вдоль длинной стороны, H рядов поперёк), раскладка — одна
    из 16 ``MatrixLayout`` (угол старта × строки/столбцы × змейка). Возвращает патч и id профиля."""
    width, height = max(1, int(width)), max(1, int(height))
    tid = next_ids(patch, "fixture_type")[0]
    ft = FixtureType(id=tid, name=name or f"Матрица {width}×{height}", kind=FixtureKind.MATRIX, cells=width * height,
                     width=width, height=height, layout=MatrixLayout(layout), color_order=color_order)
    return add_fixture_type(patch, ft), tid


def is_matrix(patch: Patch, type_id: str) -> bool:
    ft = next((t for t in patch.fixture_types if t.id == type_id), None)
    return ft is not None and ft.kind == FixtureKind.MATRIX


def line_to_rect(p1, p2, width: int, height: int) -> RectShape:
    """Матрица «вдоль линии»: прямоугольник от p1 до p2 (W клеток вдоль), толщина — H клеток того же шага."""
    L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    ang = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
    return RectShape(center=((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2), size=(L, L * height / max(width, 1)),
                     rotation_deg=ang)


def _rect_local(shape: RectShape, lx: float, ly: float):
    a = math.radians(shape.rotation_deg)
    cx, cy = shape.center
    return cx + lx * math.cos(a) - ly * math.sin(a), cy + lx * math.sin(a) + ly * math.cos(a)


def matrix_rows(patch: Patch, ids, scale: float, max_lines: int = 96) -> list:
    """Сетка клеток выбранных матриц (как у движка: строка 0 — верх) и точка первой ячейки по раскладке."""
    from dsm_core.cells import matrix_order
    rows = []
    for fx in patch.fixtures:
        if fx.id not in set(ids) or not isinstance(fx.shape, RectShape):
            continue
        ft = next((t for t in patch.fixture_types if t.id == fx.type_id), None)
        if ft is None or ft.kind != FixtureKind.MATRIX:
            continue
        s, w, h = fx.shape, ft.width, ft.height
        hw, hh = s.size[0] / 2, s.size[1] / 2
        lw = 0.6 * scale
        if w + h <= max_lines:
            for c in range(1, w):
                x = -hw + c * s.size[0] / w
                rows.append(_line_row(_rect_local(s, x, -hh), _rect_local(s, x, hh), lw, (0.8, 0.8, 0.85), 0.5))
            for r in range(1, h):
                y = hh - r * s.size[1] / h
                rows.append(_line_row(_rect_local(s, -hw, y), _rect_local(s, hw, y), lw, (0.8, 0.8, 0.85), 0.5))
        g = matrix_order(w, h, ft.layout)[0]
        r0, c0 = divmod(g, w)
        q = _rect_local(s, -hw + (c0 + 0.5) * s.size[0] / w, hh - (r0 + 0.5) * s.size[1] / h)
        d = max(min(s.size[0] / w, s.size[1] / h) * 0.6, HANDLE_PX * scale)
        rows.append(_line_row((q[0] - d / 2, q[1]), (q[0] + d / 2, q[1]), d, (0.2, 1.0, 0.4), 0.9))
    return rows



# ---------- OUTPUT MAP: физическое расположение (output_shape) ----------

def to_output_view(patch: Patch) -> Patch:
    """Патч «как на площадке»: у каждой фикстуры фигура = output_shape (если задана) иначе входная — на нём работают
    все инструменты холста OUTPUT."""
    fx = tuple(replace(f, shape=f.output_shape if f.output_shape is not None else f.shape, output_shape=None)
               for f in patch.fixtures)
    return replace(patch, fixtures=fx)


def from_output_view(view: Patch, original: Patch) -> Patch:
    """Правка, сделанная на виде OUTPUT, → настоящий патч: у существующих фикстур фигура вида уходит в output_shape,
    входная фигура (выборка цвета) остаётся прежней; совпала с входной — output_shape не пишется. Новые фикстуры
    (созданы на OUTPUT) — как есть: вход = выход, пока их не разведут. Остальные поля (имя, профиль, адрес,
    направление, удаление) берутся из вида."""
    orig = {f.id: f for f in original.fixtures}
    out = []
    for f in view.fixtures:
        o = orig.get(f.id)
        if o is None:
            out.append(f)
            continue
        out_shape = None if (o.output_shape is None and f.shape == o.shape) else f.shape
        out.append(replace(f, shape=o.shape, output_shape=out_shape))
    return replace(view, fixtures=tuple(out))



# ---------- библиотека фикстур (как Fixture Library в Resolume): ширина × высота, порядок пикселей, цвет ----------

def profile_summary(ft) -> str:
    return f"{ft.width}×{ft.height}" if ft.kind == FixtureKind.MATRIX else f"{ft.cells}×1"


def profile_dims(ft) -> tuple[int, int]:
    """Ширина × высота в пикселях: у матрицы — свои, у линейного профиля — ячеек × 1."""
    return (ft.width, ft.height) if ft.kind == FixtureKind.MATRIX else (ft.cells, 1)


def new_profile(patch: Patch, width: int = 16, height: int = 1, layout: str = "tl_rows", color_order: str = "RGB",
                name: str | None = None) -> tuple[Patch, str]:
    """Новый профиль библиотеки: высота 1 — линейный (палка/лента), больше — матрица."""
    width, height = max(1, int(width)), max(1, int(height))
    tid = next_ids(patch, "fixture_type")[0]
    if height == 1:
        ft = FixtureType(id=tid, name=name or f"Pixels {width}", kind=FixtureKind.STRIP, cells=width, color_order=color_order)
    else:
        ft = FixtureType(id=tid, name=name or f"Matrix {width}×{height}", kind=FixtureKind.MATRIX, cells=width * height,
                         width=width, height=height, layout=MatrixLayout(layout), color_order=color_order)
    return add_fixture_type(patch, ft), tid


def update_profile(patch: Patch, tid: str, width: int, height: int, layout: str, color_order: str,
                   name: str | None = None) -> Patch:
    """Правка профиля библиотеки — меняются все фикстуры профиля (как в Resolume). Высота > 1 — матрица: палки этого
    профиля становятся прямоугольниками вдоль себя; высота 1 — линейный профиль (матрица 1 ряд не нужна)."""
    width, height = max(1, int(width)), max(1, int(height))
    ft = next(t for t in patch.fixture_types if t.id == tid)
    changes = dict(color_order=color_order, name=(name or ft.name))
    if height > 1:
        changes.update(kind=FixtureKind.MATRIX, cells=width * height, width=width, height=height, layout=MatrixLayout(layout))
    else:
        kind = ft.kind if ft.kind != FixtureKind.MATRIX else FixtureKind.STRIP
        changes.update(kind=kind, cells=width, width=1, height=1)
    p = update_fixture_type(patch, tid, **changes)
    if height > 1:
        for fx in [f for f in p.fixtures if f.type_id == tid and isinstance(f.shape, LineShape)]:
            p = update_fixture(p, fx.id, shape=line_to_rect(fx.shape.p1, fx.shape.p2, width, height))
    return p


def default_shape(patch: Patch, tid: str, center, cell_px: float = 10.0):
    """Фигура новой фикстуры профиля в центре вида («Add to canvas»): линия или прямоугольник в масштабе клетки."""
    ft = next(t for t in patch.fixture_types if t.id == tid)
    w, h = profile_dims(ft)
    cx, cy = center
    if h == 1:
        L = w * cell_px / 2
        return LineShape(p1=(cx - L, cy), p2=(cx + L, cy))
    return RectShape(center=(cx, cy), size=(w * cell_px, h * cell_px), rotation_deg=0.0)
