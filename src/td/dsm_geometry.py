"""Чистая геометрия pixel-engine: позиции ячеек на холсте и строки таблиц для DAT.

Модуль не импортирует TouchDesigner и проверяется pytest вне TD. Единственная зависимость — ядро
``dsm_core`` (типы патча, сегменты, конфликты). Задача 1.2 — сигнатуры по design; тела — задачи 2.1–2.3.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from dsm_core.addressing import Segment
from dsm_core.model import Canvas, Direction, LineShape, Patch, PolylineShape, RectShape, Shape
from dsm_core.validate_types import Issue

Point = tuple[float, float]
Rows = list[list[str]]


@dataclass(frozen=True)
class GeoTables:
    """Строки таблиц, которые расширение пишет в DAT (первая строка каждой — заголовок)."""

    cells_rows: Rows = field(default_factory=list)       # строка на ячейку
    prims_rows: Rows = field(default_factory=list)       # строка на сегмент ячеек: vertices + uint-адрес
    raw_rows: Rows = field(default_factory=list)         # строка на служебный канал head/tail
    raw_prims_rows: Rows = field(default_factory=list)   # строка на сегмент head/tail
    arrows_rows: Rows = field(default_factory=list)      # стрелка направления на выводимую фикстуру
    profiles_rows: Rows = field(default_factory=list)    # prof, type_id, color_order, bits, extra
    delay_groups: Rows = field(default_factory=list)     # dgrp, frames, delay_ms
    out_groups: Rows = field(default_factory=list)       # grp, protocol, multicast, rate_hz, art_sync, output_ids
    routing_by_group: dict[str, Rows] = field(default_factory=dict)  # grp → net subnet universe netaddress priority
    warnings: tuple[str, ...] = ()                       # формы и выборки, не поддержанные в этой версии


def _polyline_points(shape: Shape) -> list[Point]:
    """Контур формы как ломаная: line — две точки; polyline — свои; rect — центральная ось вдоль ширины
    (от левого края к правому с учётом поворота) — так лента или стик на прямоугольнике идут вдоль него."""
    if isinstance(shape, LineShape):
        return [tuple(shape.p1), tuple(shape.p2)]
    if isinstance(shape, PolylineShape):
        return [tuple(pt) for pt in shape.points]
    if isinstance(shape, RectShape):
        cx, cy = shape.center
        w = shape.size[0]
        a = math.radians(shape.rotation_deg)
        dx, dy = math.cos(a) * w / 2.0, math.sin(a) * w / 2.0
        return [(cx - dx, cy - dy), (cx + dx, cy + dy)]
    raise ValueError(f"форма {shape.kind!r} не поддерживается в этой версии")


def _point_at(pts: list[Point], dist: float) -> Point:
    """Точка на ломаной на расстоянии ``dist`` от начала (по длине дуги)."""
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        seg = math.hypot(x2 - x1, y2 - y1)
        if dist <= seg or seg == 0.0:
            t = 0.0 if seg == 0.0 else dist / seg
            return (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
        dist -= seg
    return tuple(pts[-1])


def grid_for_data(n: int, direction: Direction, cell_offset: int) -> list[int]:
    """``grid_index`` для каждого ``data_index`` — как ``dsm_core.cells.cell_order`` для линейных форм."""
    grid = list(range(n))
    if direction == Direction.REVERSE:
        grid = grid[::-1]
    off = cell_offset % n if n else 0
    return [grid[(i + off) % n] for i in range(n)]


def cell_positions(shape: Shape, n: int, direction: Direction, cell_offset: int) -> list[Point]:
    """Центры ``n`` ячеек по длине дуги формы (line / polyline / rect как лента) в порядке данных.

    Позиция по форме ``g`` — на расстоянии ``(g + 0,5) / n`` длины от начала; ``direction`` и ``cell_offset``
    переводят ``data_index`` в ``grid_index`` так же, как ``dsm_core.cells.cell_order``.
    """
    if n <= 0:
        return []
    pts = _polyline_points(shape)
    if len(pts) < 2:
        raise ValueError("форме нужны хотя бы две точки")
    total = sum(math.hypot(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(pts, pts[1:]))
    by_grid = [_point_at(pts, total * (g + 0.5) / n) for g in range(n)]
    return [by_grid[g] for g in grid_for_data(n, direction, cell_offset)]


def rect_grid(shape: RectShape, width: int, height: int) -> list[Point]:
    """Центры клеток сетки ``width × height`` прямоугольника с поворотом; индекс = row * width + col,
    строка 0 — верх (большее y: ось y холста направлена вверх, как V в TD)."""
    cx, cy = shape.center
    w, h = shape.size
    a = math.radians(shape.rotation_deg)
    ca, sa = math.cos(a), math.sin(a)
    out: list[Point] = []
    for row in range(height):
        for col in range(width):
            lx = -w / 2.0 + (col + 0.5) * w / width
            ly = h / 2.0 - (row + 0.5) * h / height
            out.append((cx + lx * ca - ly * sa, cy + lx * sa + ly * ca))
    return out


def to_px(canvas: Canvas, xy: Point) -> Point:
    """Координаты формы → пиксели холста (единицы ``m`` через ``px_per_m``)."""
    if canvas.units == "m":
        k = float(canvas.px_per_m or 0.0)
        if k <= 0.0:
            raise ValueError("canvas.units = m без px_per_m")
        return (xy[0] * k, xy[1] * k)
    return (float(xy[0]), float(xy[1]))


def build_tables(patch: Patch, issues: list[Issue], segments: dict[str, list[Segment]],
                 routing: Rows, fps: float) -> GeoTables:
    """Строки всех таблиц движка из патча и таблиц ядра (сегменты, маршрутизация)."""
    raise NotImplementedError("задачи 2.2–2.3")


__all__ = ["GeoTables", "Point", "Rows", "cell_positions", "grid_for_data", "rect_grid", "to_px", "build_tables"]
