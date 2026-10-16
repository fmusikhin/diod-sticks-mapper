"""Схема документа: проверка dict и преобразование dict ↔ модель.

Все ошибки собираются списком с путём к полю (``fixtures[3].address.channel``) и причиной.
Неизвестные поля объектов складываются в ``extra`` и возвращаются в документ при ``to_dict``.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import fields as dc_fields
from typing import Any, Callable

from .errors import SchemaError
from .model import (COLOR_LETTERS, SHAPE_KINDS, Acceptance, Address, AddressMode, ArcShape, Canvas, Channel,
                    ChannelBlock, CurveShape, CustomShape, Direction, Fixture, FixtureKind, FixtureType, Group,
                    LineShape, MatrixLayout, Output, OutputRules, Patch, Point, PolylineShape, Port, RectShape,
                    Sampling, Shape, TargetKind)
from .protocol import DMX_CHANNELS, Protocol, universe_in_range, universe_range

SCHEMA_VERSION = 1

_IPV4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


class _Ctx:
    def __init__(self) -> None:
        self.errors: list[SchemaError] = []

    def err(self, path: str, reason: str) -> None:
        self.errors.append(SchemaError(path, reason))


# ---------- примитивы ----------

def _int(ctx: _Ctx, d: dict, key: str, path: str, default: Any = None, lo: int | None = None,
         hi: int | None = None, required: bool = False) -> Any:
    if key not in d or d[key] is None:
        if required:
            ctx.err(f"{path}.{key}", "обязательное поле отсутствует")
        return default
    v = d[key]
    if isinstance(v, bool) or not isinstance(v, int):
        ctx.err(f"{path}.{key}", f"ожидалось целое, получено {v!r}")
        return default
    if lo is not None and v < lo or hi is not None and v > hi:
        ctx.err(f"{path}.{key}", f"{v} вне диапазона {lo}–{hi}")
        return default
    return v


def _num(ctx: _Ctx, d: dict, key: str, path: str, default: Any = None, lo: float | None = None,
         hi: float | None = None) -> Any:
    if key not in d or d[key] is None:
        return default
    v = d[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        ctx.err(f"{path}.{key}", f"ожидалось число, получено {v!r}")
        return default
    if lo is not None and v < lo or hi is not None and v > hi:
        ctx.err(f"{path}.{key}", f"{v} вне диапазона {lo}–{hi}")
        return default
    return float(v)


def _str(ctx: _Ctx, d: dict, key: str, path: str, default: Any = None, required: bool = False) -> Any:
    if key not in d or d[key] is None:
        if required:
            ctx.err(f"{path}.{key}", "обязательное поле отсутствует")
        return default
    v = d[key]
    if not isinstance(v, str):
        ctx.err(f"{path}.{key}", f"ожидалась строка, получено {v!r}")
        return default
    if required and not v:
        ctx.err(f"{path}.{key}", "пустая строка")
        return default
    return v


def _bool(ctx: _Ctx, d: dict, key: str, path: str, default: bool) -> bool:
    if key not in d or d[key] is None:
        return default
    v = d[key]
    if not isinstance(v, bool):
        ctx.err(f"{path}.{key}", f"ожидалось true/false, получено {v!r}")
        return default
    return v


def _enum(ctx: _Ctx, d: dict, key: str, path: str, enum: type, default: Any, required: bool = False) -> Any:
    v = _str(ctx, d, key, path, None, required)
    if v is None:
        return default
    try:
        return enum(v)
    except ValueError:
        ctx.err(f"{path}.{key}", f"недопустимое значение {v!r}; допустимо: {', '.join(e.value for e in enum)}")
        return default


def _point(ctx: _Ctx, v: Any, path: str) -> Point | None:
    if (not isinstance(v, (list, tuple)) or len(v) != 2
            or any(isinstance(c, bool) or not isinstance(c, (int, float)) for c in v)):
        ctx.err(path, f"ожидалась точка [x, y], получено {v!r}")
        return None
    return (float(v[0]), float(v[1]))


def _points(ctx: _Ctx, d: dict, key: str, path: str, min_count: int) -> tuple[Point, ...] | None:
    v = d.get(key)
    if not isinstance(v, list):
        ctx.err(f"{path}.{key}", "ожидался список точек")
        return None
    pts = [_point(ctx, p, f"{path}.{key}[{i}]") for i, p in enumerate(v)]
    if any(p is None for p in pts):
        return None
    if len(pts) < min_count:
        ctx.err(f"{path}.{key}", f"нужно не меньше {min_count} точек, получено {len(pts)}")
        return None
    return tuple(pts)  # type: ignore[arg-type]


def _str_list(ctx: _Ctx, d: dict, key: str, path: str) -> tuple[str, ...]:
    v = d.get(key)
    if v is None:
        return ()
    if not isinstance(v, list) or any(not isinstance(s, str) for s in v):
        ctx.err(f"{path}.{key}", "ожидался список строк")
        return ()
    return tuple(v)


def _extra(d: dict, known: set[str]) -> dict:
    return {k: v for k, v in d.items() if k not in known}


def _known(cls: type) -> set[str]:
    return {f.name for f in dc_fields(cls)}


# ---------- блоки каналов и профиль ----------

def _channel(ctx: _Ctx, d: Any, path: str) -> Channel | None:
    if not isinstance(d, dict):
        ctx.err(path, "ожидался объект канала")
        return None
    name = _str(ctx, d, "name", path, required=True)
    role = _str(ctx, d, "role", path, "control")
    if role not in ("color", "control"):
        ctx.err(f"{path}.role", f"недопустимая роль {role!r}")
        role = "control"
    default = _int(ctx, d, "default", path, 0, 0, 255)
    bits = _int(ctx, d, "bits", path, 8)
    if bits not in (8, 16):
        ctx.err(f"{path}.bits", f"битность {bits} — допустимо 8 или 16")
        bits = 8
    if name is None:
        return None
    return Channel(name=name, role=role, default=default, bits=bits)


def _block(ctx: _Ctx, d: dict, key: str, path: str) -> ChannelBlock:
    v = d.get(key)
    if v is None:
        return ChannelBlock()
    if not isinstance(v, list):
        ctx.err(f"{path}.{key}", "ожидался список каналов")
        return ChannelBlock()
    chs = [_channel(ctx, c, f"{path}.{key}[{i}]") for i, c in enumerate(v)]
    return ChannelBlock(tuple(c for c in chs if c is not None))


def _fixture_type(ctx: _Ctx, d: Any, path: str) -> FixtureType | None:
    if not isinstance(d, dict):
        ctx.err(path, "ожидался объект профиля")
        return None
    fid = _str(ctx, d, "id", path, required=True)
    name = _str(ctx, d, "name", path, required=True)
    kind = _enum(ctx, d, "kind", path, FixtureKind, None, required=True)
    width = _int(ctx, d, "width", path, 1, 1)
    height = _int(ctx, d, "height", path, 1, 1)
    custom_cells = ()
    if kind == FixtureKind.MATRIX:
        cells = _int(ctx, d, "cells", path, width * height, 1)
        if cells != width * height:
            ctx.err(f"{path}.cells", f"у матрицы cells ({cells}) должно равняться width×height ({width * height})")
    elif kind == FixtureKind.CUSTOM:
        pts = _points(ctx, d, "custom_cells", path, 1) if "custom_cells" in d else None
        custom_cells = pts or ()
        cells = _int(ctx, d, "cells", path, len(custom_cells), 1)
        if custom_cells and cells != len(custom_cells):
            ctx.err(f"{path}.cells", f"у custom cells ({cells}) должно равняться числу custom_cells ({len(custom_cells)})")
    else:
        cells = _int(ctx, d, "cells", path, None, 1, required=True)
    layout = _enum(ctx, d, "layout", path, MatrixLayout, MatrixLayout.TL_ROWS)
    order = _str(ctx, d, "color_order", path, "RGB")
    if not order or any(c not in COLOR_LETTERS for c in order) or len(set(order)) != len(order):
        ctx.err(f"{path}.color_order", f"порядок цвета {order!r}: непустая строка из букв {COLOR_LETTERS} без повторов")
        order = "RGB"
    bits = _int(ctx, d, "bits", path, 8)
    if bits not in (8, 16):
        ctx.err(f"{path}.bits", f"битность {bits} — допустимо 8 или 16")
        bits = 8
    head = _block(ctx, d, "head", path)
    cell_extra = _block(ctx, d, "cell_extra", path)
    tail = _block(ctx, d, "tail", path)
    pitch = _num(ctx, d, "pitch_mm", path, None, 0.0)
    cpm = _num(ctx, d, "cells_per_m", path, None, 0.0)
    length = _num(ctx, d, "length_mm", path, None, 0.0)
    gamma = _num(ctx, d, "gamma", path, 1.0, 0.1, 10.0)
    wb = d.get("white_balance", [1.0, 1.0, 1.0])
    if not (isinstance(wb, list) and len(wb) == 3 and all(isinstance(x, (int, float)) for x in wb)):
        ctx.err(f"{path}.white_balance", "ожидались три числа")
        wb = [1.0, 1.0, 1.0]
    white_mode = _str(ctx, d, "white_mode", path, "none")
    watts = _num(ctx, d, "watts_per_cell", path, None, 0.0)
    if fid is None or name is None or kind is None or cells is None:
        return None
    return FixtureType(id=fid, name=name, kind=kind, cells=cells, width=width, height=height, layout=layout,
                       custom_cells=tuple(custom_cells), color_order=order, bits=bits, head=head,
                       cell_extra=cell_extra, tail=tail, pitch_mm=pitch, cells_per_m=cpm, length_mm=length,
                       gamma=gamma, white_balance=(float(wb[0]), float(wb[1]), float(wb[2])),
                       white_mode=white_mode, watts_per_cell=watts, extra=_extra(d, _known(FixtureType)))


# ---------- формы ----------

def _shape(ctx: _Ctx, d: Any, path: str, required: bool = True) -> Shape | None:
    if d is None:
        if required:
            ctx.err(path, "обязательное поле отсутствует")
        return None
    if not isinstance(d, dict):
        ctx.err(path, "ожидался объект формы")
        return None
    kind = _str(ctx, d, "kind", path, required=True)
    if kind is None:
        return None
    if kind not in SHAPE_KINDS:
        ctx.err(f"{path}.kind", f"неизвестный вид формы {kind!r}; допустимо: {', '.join(SHAPE_KINDS)}")
        return None
    if kind == "line":
        p1 = _point(ctx, d.get("p1"), f"{path}.p1")
        p2 = _point(ctx, d.get("p2"), f"{path}.p2")
        return LineShape(p1, p2) if p1 and p2 else None
    if kind == "polyline":
        pts = _points(ctx, d, "points", path, 2)
        return PolylineShape(pts) if pts else None
    if kind == "curve":
        pts = _points(ctx, d, "points", path, 4)
        return CurveShape(pts) if pts else None
    if kind == "custom":
        pts = _points(ctx, d, "points", path, 1)
        return CustomShape(pts) if pts else None
    if kind == "rect":
        c = _point(ctx, d.get("center"), f"{path}.center")
        s = _point(ctx, d.get("size"), f"{path}.size")
        rot = _num(ctx, d, "rotation_deg", path, 0.0)
        return RectShape(c, s, rot) if c and s else None
    c = _point(ctx, d.get("center"), f"{path}.center")
    r = _num(ctx, d, "radius", path, None, 0.0)
    a0 = _num(ctx, d, "start_deg", path, 0.0)
    a1 = _num(ctx, d, "end_deg", path, 360.0)
    if r is None:
        ctx.err(f"{path}.radius", "обязательное поле отсутствует")
    return ArcShape(c, r, a0, a1) if c and r is not None else None


# ---------- фикстура ----------

def _address(ctx: _Ctx, d: Any, path: str) -> Address:
    if d is None:
        return Address()
    if not isinstance(d, dict):
        ctx.err(path, "ожидался объект адреса")
        return Address()
    mode = _enum(ctx, d, "mode", path, AddressMode, AddressMode.UNASSIGNED)
    return Address(mode=mode, output_id=_str(ctx, d, "output_id", path), port_id=_str(ctx, d, "port_id", path),
                   universe=_int(ctx, d, "universe", path, None, 0),
                   channel=_int(ctx, d, "channel", path, None, 1, DMX_CHANNELS),
                   channel_offset=_int(ctx, d, "channel_offset", path, 0, 0))


def _fixture(ctx: _Ctx, d: Any, path: str) -> Fixture | None:
    if not isinstance(d, dict):
        ctx.err(path, "ожидался объект фикстуры")
        return None
    fid = _str(ctx, d, "id", path, required=True)
    name = _str(ctx, d, "name", path, required=True)
    type_id = _str(ctx, d, "type_id", path, required=True)
    shape = _shape(ctx, d.get("shape"), f"{path}.shape")
    out_shape = _shape(ctx, d.get("output_shape"), f"{path}.output_shape", required=False)
    sd = d.get("sampling") or {}
    sampling = Sampling()
    if isinstance(sd, dict):
        sk = _str(ctx, sd, "kind", f"{path}.sampling", "point")
        if sk not in ("point", "area", "elongated"):
            ctx.err(f"{path}.sampling.kind", f"недопустимая выборка {sk!r}")
            sk = "point"
        sampling = Sampling(kind=sk, size=_num(ctx, sd, "size", f"{path}.sampling", 0.0, 0.0),
                            width=_num(ctx, sd, "width", f"{path}.sampling", 0.0, 0.0),
                            along=_bool(ctx, sd, "along", f"{path}.sampling", True))
    else:
        ctx.err(f"{path}.sampling", "ожидался объект выборки")
    direction = _enum(ctx, d, "direction", path, Direction, Direction.FORWARD)
    cell_offset = _int(ctx, d, "cell_offset", path, 0)
    dis = d.get("disabled", [])
    if not isinstance(dis, list) or any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in dis):
        ctx.err(f"{path}.disabled", "ожидался список неотрицательных индексов ячеек")
        dis = []
    every_n = _int(ctx, d, "every_n", path, 1, 1)
    length = _num(ctx, d, "length_mm", path, None, 0.0)
    address = _address(ctx, d.get("address"), f"{path}.address")
    brightness = _num(ctx, d, "brightness", path, 1.0, 0.0, 1.0)
    col = d.get("color", [1.0, 1.0, 1.0])
    if not (isinstance(col, list) and len(col) == 3 and all(isinstance(x, (int, float)) for x in col)):
        ctx.err(f"{path}.color", "ожидались три числа")
        col = [1.0, 1.0, 1.0]
    mute = _bool(ctx, d, "mute", path, False)
    acc = _enum(ctx, d, "acceptance", path, Acceptance, Acceptance.NONE)
    if fid is None or name is None or type_id is None or shape is None:
        return None
    return Fixture(id=fid, name=name, type_id=type_id, shape=shape, groups=_str_list(ctx, d, "groups", path),
                   sampling=sampling, output_shape=out_shape, direction=direction, cell_offset=cell_offset,
                   disabled=frozenset(dis), every_n=every_n, length_mm=length, address=address,
                   brightness=brightness, color=(float(col[0]), float(col[1]), float(col[2])), mute=mute,
                   acceptance=acc, extra=_extra(d, _known(Fixture)))


# ---------- выход ----------

def _port(ctx: _Ctx, d: Any, path: str) -> Port | None:
    if not isinstance(d, dict):
        ctx.err(path, "ожидался объект порта")
        return None
    pid = _str(ctx, d, "id", path, required=True)
    number = _int(ctx, d, "number", path, None, 1, required=True)
    if pid is None or number is None:
        return None
    return Port(id=pid, number=number, fixture_ids=_str_list(ctx, d, "fixture_ids", path),
                new_universe=_bool(ctx, d, "new_universe", path, True),
                max_cells=_int(ctx, d, "max_cells", path, None, 1),
                max_universes=_int(ctx, d, "max_universes", path, None, 1), extra=_extra(d, _known(Port)))


def _ip(ctx: _Ctx, d: dict, path: str) -> str | None:
    v = _str(ctx, d, "ip", path)
    if v is None or v == "":
        return None
    m = _IPV4.match(v)
    if not m or any(int(g) > 255 for g in m.groups()):
        ctx.err(f"{path}.ip", f"не IPv4-адрес: {v!r}")
        return None
    return v


def _output(ctx: _Ctx, d: Any, path: str) -> Output | None:
    if not isinstance(d, dict):
        ctx.err(path, "ожидался объект выхода")
        return None
    oid = _str(ctx, d, "id", path, required=True)
    name = _str(ctx, d, "name", path, required=True)
    protocol = _enum(ctx, d, "protocol", path, Protocol, None, required=True)
    start = _int(ctx, d, "start_universe", path, None, required=True)
    if protocol is not None and start is not None and not universe_in_range(protocol, start):
        lo, hi = universe_range(protocol)
        ctx.err(f"{path}.start_universe", f"{start} вне диапазона протокола {lo}–{hi}")
    default_target = TargetKind.MULTICAST if protocol == Protocol.SACN else TargetKind.UNICAST
    target = _enum(ctx, d, "target_kind", path, TargetKind, default_target)
    if protocol == Protocol.ARTNET and target == TargetKind.MULTICAST:
        ctx.err(f"{path}.target_kind", "multicast недоступен для Art-Net")
    if protocol == Protocol.SACN and target == TargetKind.BROADCAST:
        ctx.err(f"{path}.target_kind", "broadcast недоступен для sACN")
    ip = _ip(ctx, d, path)
    rd = d.get("rules") or {}
    rules = OutputRules()
    if isinstance(rd, dict):
        rules = OutputRules(whole_pixel=_bool(ctx, rd, "whole_pixel", f"{path}.rules", True),
                            auto_span=_bool(ctx, rd, "auto_span", f"{path}.rules", True),
                            skip_511_512=_bool(ctx, rd, "skip_511_512", f"{path}.rules", False))
    else:
        ctx.err(f"{path}.rules", "ожидался объект правил")
    pv = d.get("ports", [])
    ports: list[Port] = []
    if isinstance(pv, list):
        for i, p in enumerate(pv):
            port = _port(ctx, p, f"{path}.ports[{i}]")
            if port:
                ports.append(port)
    else:
        ctx.err(f"{path}.ports", "ожидался список портов")
    if oid is None or name is None or protocol is None or start is None:
        return None
    return Output(id=oid, name=name, protocol=protocol, start_universe=start, target_kind=target, ip=ip,
                  universe_count=_int(ctx, d, "universe_count", path, None, 1), rules=rules, ports=tuple(ports),
                  fixture_ids=_str_list(ctx, d, "fixture_ids", path),
                  rate_hz=_num(ctx, d, "rate_hz", path, 60.0, 1.0, 1000.0),
                  art_sync=_bool(ctx, d, "art_sync", path, False),
                  priority=_int(ctx, d, "priority", path, 100, 0, 200),
                  delay_ms=_num(ctx, d, "delay_ms", path, 0.0, 0.0), extra=_extra(d, _known(Output)))


def _group(ctx: _Ctx, d: Any, path: str) -> Group | None:
    if not isinstance(d, dict):
        ctx.err(path, "ожидался объект группы")
        return None
    gid = _str(ctx, d, "id", path, required=True)
    name = _str(ctx, d, "name", path, required=True)
    if gid is None or name is None:
        return None
    return Group(id=gid, name=name, fixture_ids=_str_list(ctx, d, "fixture_ids", path), extra=_extra(d, _known(Group)))


def _canvas(ctx: _Ctx, d: Any, path: str) -> Canvas:
    if d is None:
        return Canvas()
    if not isinstance(d, dict):
        ctx.err(path, "ожидался объект холста")
        return Canvas()
    units = _str(ctx, d, "units", path, "px")
    if units not in ("px", "m"):
        ctx.err(f"{path}.units", f"единицы {units!r}: допустимо px или m")
        units = "px"
    bg = _str(ctx, d, "background", path)
    if bg is not None and (posixpath.isabs(bg) or re.match(r"^[A-Za-z]:[\\/]", bg) or bg.startswith("\\\\")):
        ctx.err(f"{path}.background", "путь должен быть относительным")
        bg = None
    px_per_m = _num(ctx, d, "px_per_m", path, None, 0.0)
    if units == "m" and px_per_m is None:
        ctx.err(f"{path}.px_per_m", "обязательно при единицах m")
    return Canvas(width=_int(ctx, d, "width", path, 1280, 1), height=_int(ctx, d, "height", path, 720, 1),
                  units=units, px_per_m=px_per_m, background=bg)


# ---------- документ ----------

def _list(ctx: _Ctx, doc: dict, key: str, conv: Callable[[_Ctx, Any, str], Any]) -> list:
    v = doc.get(key, [])
    if not isinstance(v, list):
        ctx.err(key, "ожидался список")
        return []
    out = []
    for i, item in enumerate(v):
        obj = conv(ctx, item, f"{key}[{i}]")
        if obj is not None:
            out.append(obj)
    return out


def _check_unique(ctx: _Ctx, items: list, key: str) -> None:
    seen: set[str] = set()
    for i, it in enumerate(items):
        if it.id in seen:
            ctx.err(f"{key}[{i}].id", f"повторяющийся id {it.id!r}")
        seen.add(it.id)


def _check_refs(ctx: _Ctx, patch: Patch) -> None:
    ft_ids = {t.id for t in patch.fixture_types}
    fx_ids = {f.id for f in patch.fixtures}
    out_ids = {o.id for o in patch.outputs}
    grp_ids = {g.id for g in patch.groups}
    port_ids = {p.id: o.id for o in patch.outputs for p in o.ports}
    for i, fx in enumerate(patch.fixtures):
        p = f"fixtures[{i}]"
        if fx.type_id not in ft_ids:
            ctx.err(f"{p}.type_id", f"профиль не найден: {fx.type_id!r}")
        for g in fx.groups:
            if g not in grp_ids:
                ctx.err(f"{p}.groups", f"группа не найдена: {g!r}")
        a = fx.address
        if a.output_id is not None and a.output_id not in out_ids:
            ctx.err(f"{p}.address.output_id", f"выход не найден: {a.output_id!r}")
        if a.port_id is not None and port_ids.get(a.port_id) != a.output_id:
            ctx.err(f"{p}.address.port_id", f"порт {a.port_id!r} не принадлежит выходу {a.output_id!r}")
        if a.mode == AddressMode.MANUAL and (a.universe is None or a.channel is None or a.output_id is None):
            ctx.err(f"{p}.address", "ручной адрес требует output_id, universe и channel")
        if a.output_id is not None:
            out = next(o for o in patch.outputs if o.id == a.output_id) if a.output_id in out_ids else None
            if out is not None:
                if a.universe is not None and not universe_in_range(out.protocol, a.universe):
                    lo, hi = universe_range(out.protocol)
                    ctx.err(f"{p}.address.universe", f"{a.universe} вне диапазона протокола {lo}–{hi}")
                lists = [fid for fid in out.fixture_ids] + [fid for pt in out.ports for fid in pt.fixture_ids]
                n = lists.count(fx.id)
                if n != 1:
                    ctx.err(f"{p}.address.output_id",
                            f"фикстура должна быть ровно в одном списке выхода {out.id!r} или его портов (найдено {n})")
    for i, out in enumerate(patch.outputs):
        p = f"outputs[{i}]"
        for fid in out.fixture_ids:
            if fid not in fx_ids:
                ctx.err(f"{p}.fixture_ids", f"фикстура не найдена: {fid!r}")
        for j, pt in enumerate(out.ports):
            for fid in pt.fixture_ids:
                if fid not in fx_ids:
                    ctx.err(f"{p}.ports[{j}].fixture_ids", f"фикстура не найдена: {fid!r}")
        _check_unique(ctx, list(out.ports), f"{p}.ports")
    for i, g in enumerate(patch.groups):
        for fid in g.fixture_ids:
            if fid not in fx_ids:
                ctx.err(f"groups[{i}].fixture_ids", f"фикстура не найдена: {fid!r}")
    # фикстура с адресом на выходе — в списке; и наоборот: списки ссылаются на фикстуры с этим output_id
    by_id = {f.id: f for f in patch.fixtures}
    for i, out in enumerate(patch.outputs):
        for fid in out.fixture_ids:
            if fid in by_id and by_id[fid].address.output_id != out.id:
                ctx.err(f"outputs[{i}].fixture_ids", f"фикстура {fid!r} не адресована на этот выход")
        for j, pt in enumerate(out.ports):
            for fid in pt.fixture_ids:
                if fid in by_id and (by_id[fid].address.output_id != out.id or by_id[fid].address.port_id != pt.id):
                    ctx.err(f"outputs[{i}].ports[{j}].fixture_ids", f"фикстура {fid!r} не адресована на этот порт")


def _parse(doc: Any) -> tuple[Patch | None, list[SchemaError]]:
    ctx = _Ctx()
    if not isinstance(doc, dict):
        ctx.err("", "документ должен быть объектом JSON")
        return None, ctx.errors
    ver = _int(ctx, doc, "schema_version", "", None, 1, required=True)
    if ver is not None and ver != SCHEMA_VERSION:
        ctx.err("schema_version", f"ожидалась версия {SCHEMA_VERSION}, получена {ver} (нужна миграция)")
    canvas = _canvas(ctx, doc.get("canvas"), "canvas")
    types = _list(ctx, doc, "fixture_types", _fixture_type)
    fixtures = _list(ctx, doc, "fixtures", _fixture)
    outputs = _list(ctx, doc, "outputs", _output)
    groups = _list(ctx, doc, "groups", _group)
    _check_unique(ctx, types, "fixture_types")
    _check_unique(ctx, fixtures, "fixtures")
    _check_unique(ctx, outputs, "outputs")
    _check_unique(ctx, groups, "groups")
    known = {"schema_version", "canvas", "fixture_types", "fixtures", "outputs", "groups"}
    patch = Patch(schema_version=SCHEMA_VERSION, canvas=canvas, fixture_types=tuple(types), fixtures=tuple(fixtures),
                  outputs=tuple(outputs), groups=tuple(groups), extra=_extra(doc, known))
    if not ctx.errors:
        _check_refs(ctx, patch)
    return patch, ctx.errors


def validate_document(doc: Any) -> list[SchemaError]:
    """Все ошибки схемы документа (пустой список — документ валиден)."""
    return _parse(doc)[1]


def from_dict(doc: Any) -> Patch:
    """Документ → модель. При ошибках бросает первую ``SchemaError`` с полным списком в ``.errors``."""
    patch, errors = _parse(doc)
    if errors:
        first = errors[0]
        raise SchemaError(first.path, first.reason, errors)
    assert patch is not None
    return patch


# ---------- модель → dict ----------

def _r(x: float) -> float:
    return round(float(x), 3)


def _pt(p: Point) -> list[float]:
    return [_r(p[0]), _r(p[1])]


def _channel_d(c: Channel) -> dict:
    return {"name": c.name, "role": c.role, "default": c.default, "bits": c.bits}


def _shape_d(s: Shape | None) -> dict | None:
    if s is None:
        return None
    if isinstance(s, LineShape):
        return {"kind": "line", "p1": _pt(s.p1), "p2": _pt(s.p2)}
    if isinstance(s, (PolylineShape, CurveShape, CustomShape)):
        return {"kind": s.kind, "points": [_pt(p) for p in s.points]}
    if isinstance(s, RectShape):
        return {"kind": "rect", "center": _pt(s.center), "size": _pt(s.size), "rotation_deg": _r(s.rotation_deg)}
    return {"kind": "arc", "center": _pt(s.center), "radius": _r(s.radius), "start_deg": _r(s.start_deg),
            "end_deg": _r(s.end_deg)}


def _merge(extra: dict, known: dict) -> dict:
    out = dict(extra)
    out.update(known)
    return out


def _fixture_type_d(t: FixtureType) -> dict:
    d = {"id": t.id, "name": t.name, "kind": t.kind.value, "cells": t.cells, "width": t.width, "height": t.height,
         "layout": t.layout.value, "color_order": t.color_order, "bits": t.bits,
         "head": [_channel_d(c) for c in t.head.channels], "cell_extra": [_channel_d(c) for c in t.cell_extra.channels],
         "tail": [_channel_d(c) for c in t.tail.channels], "pitch_mm": t.pitch_mm, "cells_per_m": t.cells_per_m,
         "length_mm": t.length_mm, "gamma": t.gamma, "white_balance": [_r(x) for x in t.white_balance],
         "white_mode": t.white_mode, "watts_per_cell": t.watts_per_cell}
    if t.custom_cells:
        d["custom_cells"] = [_pt(p) for p in t.custom_cells]
    return _merge(t.extra, d)


def _fixture_d(f: Fixture) -> dict:
    a = f.address
    d = {"id": f.id, "name": f.name, "type_id": f.type_id, "groups": list(f.groups), "shape": _shape_d(f.shape),
         "sampling": {"kind": f.sampling.kind, "size": _r(f.sampling.size), "width": _r(f.sampling.width),
                      "along": f.sampling.along},
         "direction": f.direction.value, "cell_offset": f.cell_offset, "disabled": sorted(f.disabled),
         "every_n": f.every_n, "length_mm": f.length_mm,
         "address": {"mode": a.mode.value, "output_id": a.output_id, "port_id": a.port_id, "universe": a.universe,
                     "channel": a.channel, "channel_offset": a.channel_offset},
         "brightness": _r(f.brightness), "color": [_r(x) for x in f.color], "mute": f.mute,
         "acceptance": f.acceptance.value}
    if f.output_shape is not None:
        d["output_shape"] = _shape_d(f.output_shape)
    return _merge(f.extra, d)


def _port_d(p: Port) -> dict:
    return _merge(p.extra, {"id": p.id, "number": p.number, "fixture_ids": list(p.fixture_ids),
                            "new_universe": p.new_universe, "max_cells": p.max_cells, "max_universes": p.max_universes})


def _output_d(o: Output) -> dict:
    return _merge(o.extra, {
        "id": o.id, "name": o.name, "protocol": o.protocol.value, "target_kind": o.target_kind.value, "ip": o.ip,
        "start_universe": o.start_universe, "universe_count": o.universe_count,
        "rules": {"whole_pixel": o.rules.whole_pixel, "auto_span": o.rules.auto_span, "skip_511_512": o.rules.skip_511_512},
        "ports": [_port_d(p) for p in o.ports], "fixture_ids": list(o.fixture_ids), "rate_hz": _r(o.rate_hz),
        "art_sync": o.art_sync, "priority": o.priority, "delay_ms": _r(o.delay_ms)})


def to_dict(patch: Patch) -> dict:
    """Модель → документ; известные поля поверх ``extra``."""
    c = patch.canvas
    canvas = {"width": c.width, "height": c.height, "units": c.units, "px_per_m": c.px_per_m, "background": c.background}
    return _merge(patch.extra, {
        "schema_version": SCHEMA_VERSION, "canvas": canvas,
        "fixture_types": [_fixture_type_d(t) for t in patch.fixture_types],
        "fixtures": [_fixture_d(f) for f in patch.fixtures],
        "outputs": [_output_d(o) for o in patch.outputs],
        "groups": [_merge(g.extra, {"id": g.id, "name": g.name, "fixture_ids": list(g.fixture_ids)}) for g in patch.groups]})
