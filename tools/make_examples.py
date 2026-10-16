"""Генерирует примеры патчей в examples/ через API ядра (запускать из корня репозитория).

usage: python tools/make_examples.py
Все цели — без IP (вывод на 127.0.0.1). Данные синтетические.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "core"))

from dsm_core import api  # noqa: E402


def strip_ws2812b() -> api.Patch:
    p = api.new_patch()
    p = api.add_fixture_type(p, api.preset_fixture_type("ws2812b_grb", "ft1"))
    p = api.add_output(p, api.Output(id="out1", name="Лента", protocol=api.Protocol.ARTNET, start_universe=0))
    p = api.add_fixture(p, api.Fixture(id="fx1", name="Лента 5 м", type_id="ft1", length_mm=5000,
                                       shape=api.LineShape((100.0, 360.0), (1180.0, 360.0)),
                                       address=api.Address(api.AddressMode.AUTO, "out1")))
    return api.auto_assign_all(p)[0]


def sticks_8x_artnet() -> api.Patch:
    p = api.new_patch()
    p = api.add_fixture_type(p, api.preset_fixture_type("stick_1m_144", "ft1"))
    ports = (api.Port(id="port1", number=1, max_cells=1020, max_universes=6),
             api.Port(id="port2", number=2, max_cells=1020, max_universes=6))
    p = api.add_output(p, api.Output(id="out1", name="Контроллер стиков", protocol=api.Protocol.ARTNET,
                                     start_universe=0, ports=ports))
    for i in range(8):
        port = "port1" if i < 4 else "port2"
        x = 160.0 + i * 140.0
        p = api.add_fixture(p, api.Fixture(id=f"fx{i + 1}", name=f"Стик {i + 1}", type_id="ft1",
                                           shape=api.LineShape((x, 100.0), (x, 620.0)),
                                           address=api.Address(api.AddressMode.AUTO, "out1", port)))
    return api.auto_assign_all(p)[0]


def matrix_16x16_sacn() -> api.Patch:
    p = api.new_patch()
    p = api.add_fixture_type(p, api.preset_fixture_type("matrix_16x16", "ft1"))
    p = api.add_output(p, api.Output(id="out1", name="Матрица", protocol=api.Protocol.SACN, start_universe=1,
                                     target_kind=api.TargetKind.MULTICAST, priority=100))
    p = api.add_fixture(p, api.Fixture(id="fx1", name="Матрица 16×16", type_id="ft1",
                                       shape=api.RectShape((640.0, 360.0), (320.0, 320.0), 0.0),
                                       address=api.Address(api.AddressMode.AUTO, "out1")))
    return api.auto_assign_all(p)[0]


def bar_common_channels() -> api.Patch:
    p = api.new_patch()
    p = api.add_fixture_type(p, api.preset_fixture_type("bar_common", "ft1"))
    p = api.add_output(p, api.Output(id="out1", name="Бары", protocol=api.Protocol.ARTNET, start_universe=0))
    for i in range(4):
        y = 150.0 + i * 140.0
        p = api.add_fixture(p, api.Fixture(id=f"fx{i + 1}", name=f"Бар {i + 1}", type_id="ft1",
                                           shape=api.LineShape((200.0, y), (1080.0, y)),
                                           address=api.Address(api.AddressMode.AUTO, "out1")))
    p = api.add_group(p, api.Group(id="grp1", name="Все бары", fixture_ids=("fx1", "fx2", "fx3", "fx4")))
    return api.auto_assign_all(p)[0]


EXAMPLES = {"strip_ws2812b": strip_ws2812b, "sticks_8x_artnet": sticks_8x_artnet,
            "matrix_16x16_sacn": matrix_16x16_sacn, "bar_common_channels": bar_common_channels}


def main() -> None:
    out = ROOT / "examples"
    out.mkdir(exist_ok=True)
    for name, fn in EXAMPLES.items():
        patch = fn()
        api.save(patch, out / f"{name}.json")
        print(name, api.summary(patch))


if __name__ == "__main__":
    main()
