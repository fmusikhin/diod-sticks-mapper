"""Встроенные пресеты: профили фикстур и числа для проверки портов. Только данные, без привязки к железу."""
from __future__ import annotations

from dataclasses import replace

from .model import Channel, ChannelBlock, FixtureKind, FixtureType, MatrixLayout

FIXTURE_PRESETS: tuple[FixtureType, ...] = (
    FixtureType(id="ws2811_rgb", name="WS2811 RGB 60/м", kind=FixtureKind.STRIP, cells=60,
                color_order="RGB", cells_per_m=60, pitch_mm=1000 / 60),
    FixtureType(id="ws2812b_grb", name="WS2812B GRB 60/м", kind=FixtureKind.STRIP, cells=60,
                color_order="GRB", cells_per_m=60, pitch_mm=1000 / 60),
    FixtureType(id="sk6812_rgbw", name="SK6812 RGBW 60/м", kind=FixtureKind.STRIP, cells=60,
                color_order="GRBW", cells_per_m=60, pitch_mm=1000 / 60),
    FixtureType(id="tm1934_rgb", name="TM1934 RGB 60/м", kind=FixtureKind.STRIP, cells=60,
                color_order="RGB", cells_per_m=60, pitch_mm=1000 / 60),
    FixtureType(id="stick_1m_144", name="Стик 1 м 144 px", kind=FixtureKind.STICK, cells=144,
                color_order="GRB", cells_per_m=144, pitch_mm=1000 / 144, length_mm=1000),
    FixtureType(id="matrix_16x16", name="Матрица 16×16", kind=FixtureKind.MATRIX, cells=256, width=16, height=16,
                layout=MatrixLayout.TL_ROWS_SNAKE, color_order="GRB"),
    FixtureType(id="bar_common", name="Бар 8 ячеек с общими каналами", kind=FixtureKind.BAR, cells=8,
                color_order="RGB",
                head=ChannelBlock((Channel("dim", default=255), Channel("strobe"))),
                tail=ChannelBlock((Channel("mode"),))),
)

PORT_PRESETS: dict[str, dict[str, int]] = {
    "generic_pixel_port": {"max_cells": 1020, "max_universes": 6},
    "single_universe": {"max_cells": 170, "max_universes": 1},
}


def preset_fixture_type(preset_id: str, new_id: str) -> FixtureType:
    for ft in FIXTURE_PRESETS:
        if ft.id == preset_id:
            return replace(ft, id=new_id)
    raise KeyError(f"пресет не найден: {preset_id}")
