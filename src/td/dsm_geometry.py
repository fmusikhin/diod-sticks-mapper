"""Чистая геометрия pixel-engine: позиции ячеек на холсте и строки таблиц для DAT.

Модуль не импортирует TouchDesigner и проверяется pytest вне TD. Единственная зависимость — ядро
``dsm_core`` (типы патча, сегменты, конфликты). Сигнатуры — по design; ось y холста направлена вверх
(как V в TD), поворот — против часовой стрелки.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from dsm_core.addressing import Segment
from dsm_core.cells import cell_order
from dsm_core.model import (Canvas, Direction, FixtureKind, LineShape, Patch, PolylineShape, RectShape, Shape,
                            TargetKind, effective_target_ip)
from dsm_core.protocol import Protocol, from_port_address
from dsm_core.validate_types import Issue, IssueLevel

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


# ---------- позиции ячеек (2.1) ----------

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


# ---------- строки таблиц (2.2–2.3) ----------

CELL_COLUMNS = ["P(0)", "P(1)", "P(2)", "Tex(0)", "Tex(1)", "Tex(2)", "cell", "fx", "prof", "dgrp", "active", "bright",
                "col(0)", "col(1)", "col(2)", "mute", "Color(0)", "Color(1)", "Color(2)", "Color(3)"]
PRIM_COLUMNS = ["vertices", "fx", "seg", "DMXFixtureUniverse", "DMXFixtureChannel",
                "DMXFixtureNetAddress(0)", "DMXFixtureNetAddress(1)", "DMXFixtureNetAddress(2)", "DMXFixtureNetAddress(3)"]
RAW_COLUMNS = ["v", "fx", "dgrp"]
ARROW_COLUMNS = ["fx", "fixture_id", "x1", "y1", "x2", "y2"]
PROFILE_COLUMNS = ["prof", "type_id", "name", "color_order", "bits", "channels_per_cell", "extra_defaults"]
DELAY_COLUMNS = ["dgrp", "frames", "delay_ms"]
OUT_GROUP_COLUMNS = ["grp", "protocol", "multicast", "rate_hz", "art_sync", "output_ids"]
ROUTING_COLUMNS = ["net", "subnet", "universe", "netaddress", "priority", "kinetport"]
SUPPORTED_SHAPES = ("line", "polyline", "rect")


def _f(x: float) -> str:
    return repr(round(float(x), 6))


def _block_values(block) -> list[float]:
    """Значения служебных каналов блока в порядке DMX (16 бит — старший, младший)."""
    vals: list[float] = []
    for ch in block.channels:
        if ch.bits == 16:
            vals.extend([ch.default / 255.0, 0.0])
        else:
            vals.append(ch.default / 255.0)
    return vals


def _positions_by_grid(canvas: Canvas, ft, fx, n: int) -> list[Point]:
    """Позиции ячеек по grid_index в пикселях холста: матрица на rect — сетка, иначе — вдоль формы."""
    if ft.kind == FixtureKind.MATRIX and isinstance(fx.shape, RectShape):
        pts = rect_grid(fx.shape, ft.width, ft.height)
        if len(pts) != n:
            pts = cell_positions(fx.shape, n, Direction.FORWARD, 0)
    else:
        pts = cell_positions(fx.shape, n, Direction.FORWARD, 0)
    return [to_px(canvas, p) for p in pts]


BROADCAST_IP = "255.255.255.255"


def _target_ip(out, universe: int, ip_of: dict[tuple[str, int], str]) -> str:
    """Цель юниверса: из маршрутизации ядра (IP, multicast-группа sACN или 127.0.0.1 без цели);
    broadcast без IP — 255.255.255.255 (явный выбор пользователя, как multicast)."""
    if out.target_kind == TargetKind.BROADCAST and not out.ip:
        return BROADCAST_IP
    return ip_of.get((out.id, universe)) or effective_target_ip(out)


def _delay_groups(patch: Patch, fps: float) -> tuple[dict[str, int], dict[int, tuple[int, float]]]:
    """output_id → dgrp; dgrp → (кадров, delay_ms). Группа 0 — без задержки."""
    delay_of: dict[str, int] = {}
    groups: dict[int, tuple[int, float]] = {0: (0, 0.0)}
    for out in patch.outputs:
        frames = int(round(out.delay_ms * fps / 1000.0)) if out.delay_ms > 0 else 0
        if frames == 0:
            delay_of[out.id] = 0
            continue
        k = next((g for g, (fr, _) in groups.items() if fr == frames), None)
        if k is None:
            k = len(groups)
            groups[k] = (frames, out.delay_ms)
        delay_of[out.id] = k
    return delay_of, groups


def _out_groups(patch: Patch) -> dict[tuple, list[str]]:
    """(protocol, multicast, rate_hz, art_sync) → выходы группы, в порядке патча."""
    groups: dict[tuple, list[str]] = {}
    for out in patch.outputs:
        key = (out.protocol.value, out.target_kind == TargetKind.MULTICAST, float(out.rate_hz), bool(out.art_sync))
        groups.setdefault(key, []).append(out.id)
    return groups


def build_tables(patch: Patch, issues: list[Issue], segments: dict[str, list[Segment]],
                 routing: Rows, fps: float) -> GeoTables:
    """Строки всех таблиц движка из патча и таблиц ядра (сегменты, маршрутизация).

    Из вывода исключаются фикстуры с конфликтами уровня error, без адреса и с неподдержанной формой;
    выборка ``area``/``elongated`` берётся как ``point`` с предупреждением (решения заказчика).
    """
    warnings: list[str] = []
    types = {t.id: t for t in patch.fixture_types}
    outputs = {o.id: o for o in patch.outputs}
    bad: set[str] = set()
    for iss in issues:
        if iss.level == IssueLevel.ERROR and iss.fixture_ids:
            bad.update(iss.fixture_ids)
            warnings.append(f"не выводится ({iss.code.value}): {', '.join(iss.fixture_ids)} — {iss.message}")

    col = {name: k for k, name in enumerate(routing[0])} if routing else {}
    ip_of: dict[tuple[str, int], str] = {}
    for row in routing[1:]:
        ip_of[(row[col["output_id"]], int(row[col["universe"]]))] = row[col["ip"]]

    delay_of, frames_of_group = _delay_groups(patch, fps)
    groups = _out_groups(patch)
    for ids in groups.values():
        missing = [oid for oid in ids if outputs[oid].ip is None and outputs[oid].target_kind == TargetKind.UNICAST]
        if missing:
            warnings.append(f"target_missing: цель не задана, вывод на 127.0.0.1: {', '.join(missing)}")

    prof_index: dict[str, int] = {}
    profiles_rows: Rows = [PROFILE_COLUMNS[:]]
    for k, ft in enumerate(patch.fixture_types):
        prof_index[ft.id] = k
        extra = ";".join(_f(v) for v in _block_values(ft.cell_extra))
        profiles_rows.append([str(k), ft.id, ft.name, ft.color_order, str(ft.bits), str(ft.channels_per_cell), extra])

    cells_rows: Rows = [CELL_COLUMNS[:]]
    prims_rows: Rows = [PRIM_COLUMNS[:]]
    raw_rows: Rows = [RAW_COLUMNS[:]]
    raw_prims_rows: Rows = [PRIM_COLUMNS[:]]
    arrows_rows: Rows = [ARROW_COLUMNS[:]]

    for fx_i, fx in enumerate(patch.fixtures):
        if fx.id in bad:
            continue
        ft = types.get(fx.type_id)
        out = outputs.get(fx.address.output_id or "")
        segs = segments.get(fx.id, [])
        if ft is None or out is None or not segs:
            warnings.append(f"без адреса, не выводится: {fx.id}")
            continue
        if fx.shape.kind not in SUPPORTED_SHAPES:
            warnings.append(f"форма {fx.shape.kind!r} не поддерживается в этой версии: {fx.id}")
            continue
        if fx.sampling.kind != "point":
            warnings.append(f"выборка {fx.sampling.kind!r} — этап 2, берётся как point: {fx.id}")
        order = cell_order(ft, fx)
        n = len(order)
        by_grid = _positions_by_grid(patch.canvas, ft, fx, n)
        pos = [by_grid[c.grid_index] for c in order]
        dgrp, prof = delay_of[out.id], prof_index[ft.id]
        bright = _f(fx.brightness)
        colr, colg, colb = (_f(c) for c in fx.color)
        mute = "1" if fx.mute else "0"
        placed: set[int] = set()
        for seg_i, sg in enumerate(segs):
            octets = _target_ip(out, sg.universe, ip_of).split(".")
            addr = [str(sg.universe), str(sg.first_channel), *octets]
            if sg.kind == "cells":
                # whole_pixel = false: ячейка на границе юниверса приходит в двух partial-сегментах;
                # движок кладёт её в первый (адрес второй половины не выводится — ядро даёт cell_on_boundary)
                idx = [sg.first_cell + k for k in range(sg.n_cells) if sg.first_cell + k not in placed]
                if not idx:
                    continue
                verts: list[str] = []
                for di in idx:
                    c = order[di]
                    x, y = pos[di]
                    cells_rows.append([_f(x), _f(y), "0", _f(x / patch.canvas.width), _f(y / patch.canvas.height), "0",
                                       str(di), str(fx_i), str(prof), str(dgrp), "1" if c.active else "0",
                                       bright, colr, colg, colb, mute, "0", "0", "0", "1"])
                    verts.append(str(len(cells_rows) - 2))
                    placed.add(di)
                prims_rows.append([" ".join(verts), str(fx_i), str(seg_i), *addr])
            else:
                block = ft.head if sg.kind == "head" else ft.tail
                vals = _block_values(block)[: sg.last_channel - sg.first_channel + 1]
                verts = []
                for v in vals:
                    raw_rows.append([_f(v), str(fx_i), str(dgrp)])
                    verts.append(str(len(raw_rows) - 2))
                raw_prims_rows.append([" ".join(verts), str(fx_i), str(seg_i), *addr])
        if n:
            (x1, y1), (x2, y2) = pos[0], pos[-1]
            arrows_rows.append([str(fx_i), fx.id, _f(x1), _f(y1), _f(x2), _f(y2)])

    delay_rows: Rows = [DELAY_COLUMNS[:]] + [[str(k), str(fr), _f(ms)] for k, (fr, ms) in sorted(frames_of_group.items())]
    out_rows: Rows = [OUT_GROUP_COLUMNS[:]]
    routing_by_group: dict[str, Rows] = {}
    for k, (key, ids) in enumerate(groups.items()):
        grp = f"g{k}"
        protocol, multicast, rate, art_sync = key
        out_rows.append([grp, protocol, "1" if multicast else "0", _f(rate), "1" if art_sync else "0", " ".join(ids)])
        rows: Rows = [ROUTING_COLUMNS[:]]
        for row in routing[1:]:
            if row[col["output_id"]] not in ids:
                continue
            u = int(row[col["universe"]])
            net, subnet, uni = from_port_address(u) if protocol == Protocol.ARTNET.value else (0, 0, u)
            ip = _target_ip(outputs[row[col["output_id"]]], u, ip_of)
            rows.append([str(net), str(subnet), str(uni), ip, row[col["priority"]], ""])
        routing_by_group[grp] = rows

    return GeoTables(cells_rows=cells_rows, prims_rows=prims_rows, raw_rows=raw_rows, raw_prims_rows=raw_prims_rows,
                     arrows_rows=arrows_rows, profiles_rows=profiles_rows, delay_groups=delay_rows,
                     out_groups=out_rows, routing_by_group=routing_by_group, warnings=tuple(warnings))


__all__ = ["GeoTables", "Point", "Rows", "cell_positions", "grid_for_data", "rect_grid", "to_px", "build_tables",
           "CELL_COLUMNS", "PRIM_COLUMNS", "RAW_COLUMNS", "ARROW_COLUMNS", "PROFILE_COLUMNS", "DELAY_COLUMNS",
           "OUT_GROUP_COLUMNS", "ROUTING_COLUMNS"]
