"""Чистая логика Locate (locate-test): разбор цели, порядок обхода, строки таблицы на фикстуру, раскладка номера
и адреса, сырой адрес, лист приёмки, эталон цвета ячейки для стенда.

Без TD-API и без состояния между вызовами: функции от патча ядра (``dsm_core``), его проверок и сегментов.
Индекс фикстуры ``fx`` = индекс в ``patch.fixtures`` (как в таблице ``geo_cells`` движка).
"""
from __future__ import annotations

# колонки таблицы tables/locate_fx — строка на фикстуру патча (design: «tables/locate_fx»)
FX_COLUMNS = ["fx", "target", "number", "uni", "ch", "ncells", "offset"]

MODES = ("blink", "direction", "number", "address")
OTHERS = ("solo", "dim", "over")
PATTERNS = ("off", "fill", "rgbw", "chase")
