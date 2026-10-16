"""Порядок ячеек фикстуры в данных DMX и их активность.

Позиции ячеек на холсте здесь не считаются (это ``pixel-engine``): ядро отдаёт только
соответствие «индекс в данных ↔ индекс на форме/в сетке» и флаг активности.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .model import Direction, Fixture, FixtureKind, FixtureType, MatrixLayout


@dataclass(frozen=True, slots=True)
class Cell:
    data_index: int   # позиция в потоке DMX, 0..n-1
    grid_index: int   # позиция на форме; для matrix = row * width + col (строка 0 — верх)
    active: bool      # False — отключена или не попадает под «каждая N-я»


def cell_count(ft: FixtureType, fx: Fixture) -> int:
    """Число ячеек: у ленты с заданной длиной — из длины и шага (вниз), иначе из профиля."""
    if ft.kind == FixtureKind.STRIP and fx.length_mm:
        if ft.pitch_mm:
            return max(1, math.floor(fx.length_mm / ft.pitch_mm))
        if ft.cells_per_m:
            return max(1, math.floor(fx.length_mm / 1000.0 * ft.cells_per_m))
    return ft.cells


def matrix_order(width: int, height: int, layout: MatrixLayout) -> list[int]:
    """grid_index для каждого data_index по одной из 16 раскладок."""
    name = layout.value                      # напр. "tr_cols_snake"
    corner, line = name.split("_")[0], name.split("_")[1]
    snake = name.endswith("_snake")
    top = corner[0] == "t"
    left = corner[1] == "l"
    rows = list(range(height)) if top else list(range(height - 1, -1, -1))
    cols = list(range(width)) if left else list(range(width - 1, -1, -1))
    out: list[int] = []
    if line == "rows":
        for k, r in enumerate(rows):
            line_cols = cols if not (snake and k % 2) else cols[::-1]
            out.extend(r * width + c for c in line_cols)
    else:
        for k, c in enumerate(cols):
            line_rows = rows if not (snake and k % 2) else rows[::-1]
            out.extend(r * width + c for r in line_rows)
    return out


def cell_order(ft: FixtureType, fx: Fixture) -> list[Cell]:
    n = cell_count(ft, fx)
    if ft.kind == FixtureKind.MATRIX:
        grid = matrix_order(ft.width, ft.height, ft.layout)
        if len(grid) != n:
            grid = list(range(n))
    else:
        grid = list(range(n))
    if fx.direction == Direction.REVERSE:
        grid = grid[::-1]
    off = fx.cell_offset % n if n else 0
    cells = []
    for i in range(n):
        active = i not in fx.disabled and (fx.every_n <= 1 or i % fx.every_n == 0)
        cells.append(Cell(data_index=i, grid_index=grid[(i + off) % n], active=active))
    return cells
