"""Чистая геометрия pixel-engine: позиции ячеек на холсте и строки таблиц для DAT.

Модуль не импортирует TouchDesigner и проверяется pytest вне TD. Единственная зависимость — ядро
``dsm_core`` (типы патча, сегменты, конфликты). Задача 1.2 — сигнатуры по design; тела — задачи 2.1–2.3.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from dsm_core.addressing import Segment
from dsm_core.model import Canvas, Direction, Patch, RectShape, Shape
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


def cell_positions(shape: Shape, n: int, direction: Direction, cell_offset: int) -> list[Point]:
    """Центры ``n`` ячеек по длине дуги формы (line / polyline / rect как контур → как лента) в порядке данных.

    Позиция по форме ``g`` — на расстоянии ``(g + 0,5) / n`` длины от начала; ``direction`` и ``cell_offset``
    переводят ``data_index`` в ``grid_index`` так же, как ``dsm_core.cells.cell_order``.
    """
    raise NotImplementedError("задача 2.1")


def rect_grid(shape: RectShape, width: int, height: int) -> list[Point]:
    """Центры клеток сетки ``width × height`` прямоугольника с поворотом; индекс = row * width + col."""
    raise NotImplementedError("задача 2.1")


def to_px(canvas: Canvas, xy: Point) -> Point:
    """Координаты формы → пиксели холста (единицы ``m`` через ``px_per_m``)."""
    raise NotImplementedError("задача 2.1")


def build_tables(patch: Patch, issues: list[Issue], segments: dict[str, list[Segment]],
                 routing: Rows, fps: float) -> GeoTables:
    """Строки всех таблиц движка из патча и таблиц ядра (сегменты, маршрутизация)."""
    raise NotImplementedError("задачи 2.2–2.3")


__all__ = ["GeoTables", "Point", "Rows", "cell_positions", "rect_grid", "to_px", "build_tables"]
