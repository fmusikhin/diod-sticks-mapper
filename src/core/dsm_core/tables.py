"""Таблицы из патча: строки одинаковой длины с заголовком, числа — строками без локали (годится для DAT)."""
from __future__ import annotations

from .addressing import Segment, fixture_segments
from .cells import cell_count, cell_order
from .errors import LayoutError
from .model import Patch, TargetKind, effective_target_ip
from .protocol import Protocol, dotted, sacn_multicast
from .validate import validate
from .validate_types import IssueLevel

Rows = list[list[str]]


def _s(x) -> str:
    if x is None:
        return ""
    if isinstance(x, bool):
        return "1" if x else "0"
    if isinstance(x, float):
        return repr(x) if x != int(x) else str(int(x))
    return str(x)


def _dotted(protocol: Protocol, universe: int | None) -> str:
    if universe is None:
        return ""
    return dotted(universe) if protocol == Protocol.ARTNET else ""


def _segs(patch: Patch, fx) -> list[Segment]:
    try:
        return fixture_segments(patch, fx)
    except LayoutError:
        return []


ADDRESS_COLUMNS = ["fixture_id", "name", "type", "output", "protocol", "universe", "universe_dotted", "channel",
                   "cells", "channels", "universes", "mode", "acceptance"]


def address_table(patch: Patch) -> Rows:
    rows: Rows = [ADDRESS_COLUMNS[:]]
    types = {t.id: t for t in patch.fixture_types}
    outs = {o.id: o for o in patch.outputs}
    for fx in patch.fixtures:
        ft = types.get(fx.type_id)
        out = outs.get(fx.address.output_id) if fx.address.output_id else None
        segs = _segs(patch, fx) if ft and out else []
        by_u: dict[int, list[Segment]] = {}
        for s in segs:
            by_u.setdefault(s.universe, []).append(s)
        unis = ";".join(f"{u}:{min(s.first_channel for s in ss)}-{max(s.last_channel for s in ss)}" for u, ss in sorted(by_u.items()))
        rows.append([fx.id, fx.name, ft.name if ft else fx.type_id, out.name if out else "", out.protocol.value if out else "",
                     _s(fx.address.universe), _dotted(out.protocol, fx.address.universe) if out else "",
                     _s(fx.address.channel), _s(cell_count(ft, fx) if ft else 0),
                     _s(ft.head.width + cell_count(ft, fx) * ft.channels_per_cell + ft.tail.width if ft else 0),
                     unis, fx.address.mode.value, fx.acceptance.value])
    return rows


SEGMENT_COLUMNS = ["fixture_id", "universe", "first_channel", "last_channel", "kind", "first_cell", "n_cells", "partial"]


def segment_table(patch: Patch) -> Rows:
    rows: Rows = [SEGMENT_COLUMNS[:]]
    for fx in patch.fixtures:
        for s in _segs(patch, fx):
            rows.append([fx.id, _s(s.universe), _s(s.first_channel), _s(s.last_channel), s.kind, _s(s.first_cell),
                         _s(s.n_cells), _s(s.partial)])
    return rows


CELL_COLUMNS = ["fixture_id", "data_index", "grid_index", "universe", "first_channel", "channels", "active"]


def cell_table(patch: Patch) -> Rows:
    """По строке на ячейку; строится кортежами, не хранится в патче."""
    rows: Rows = [CELL_COLUMNS[:]]
    types = {t.id: t for t in patch.fixture_types}
    for fx in patch.fixtures:
        ft = types.get(fx.type_id)
        if ft is None:
            continue
        cpc = ft.channels_per_cell
        cells = cell_order(ft, fx)
        segs = [s for s in _segs(patch, fx) if s.kind == "cells"]
        pos: dict[int, tuple[int, int]] = {}
        for s in segs:
            for k in range(s.n_cells):
                if not s.partial:
                    pos[s.first_cell + k] = (s.universe, s.first_channel + k * cpc)
                elif s.first_cell + k not in pos:
                    pos[s.first_cell + k] = (s.universe, s.first_channel + max(0, k * cpc - 0))
        fid, cpc_s = fx.id, str(cpc)
        for c in cells:
            u, ch = pos.get(c.data_index, (None, None))
            rows.append([fid, str(c.data_index), str(c.grid_index), _s(u), _s(ch), cpc_s, "1" if c.active else "0"])
    return rows


ROUTING_COLUMNS = ["output_id", "name", "protocol", "universe", "universe_dotted", "target_kind", "ip", "priority",
                   "delay_ms", "rate_hz", "art_sync"]


def routing_table(patch: Patch) -> Rows:
    rows: Rows = [ROUTING_COLUMNS[:]]
    for out in patch.outputs:
        unis: set[int] = set()
        for fx in patch.fixtures:
            if fx.address.output_id == out.id:
                unis.update(s.universe for s in _segs(patch, fx))
        if out.universe_count:
            unis.update(range(out.start_universe, out.start_universe + out.universe_count))
        for u in sorted(unis):
            if out.protocol == Protocol.SACN and out.target_kind == TargetKind.MULTICAST and not out.ip:
                ip = sacn_multicast(u)
            else:
                ip = effective_target_ip(out)
            rows.append([out.id, out.name, out.protocol.value, str(u), _dotted(out.protocol, u), out.target_kind.value, ip,
                         _s(out.priority), _s(out.delay_ms), _s(out.rate_hz), _s(out.art_sync)])
    return rows


def summary(patch: Patch) -> dict[str, int]:
    types = {t.id: t for t in patch.fixture_types}
    cells = channels = 0
    unis: set[tuple[str, int]] = set()
    for fx in patch.fixtures:
        ft = types.get(fx.type_id)
        if ft is None:
            continue
        n = cell_count(ft, fx)
        cells += n
        channels += ft.head.width + n * ft.channels_per_cell + ft.tail.width
        unis.update((fx.address.output_id or "", s.universe) for s in _segs(patch, fx))
    issues = validate(patch)
    return {"fixtures": len(patch.fixtures), "cells": cells, "channels": channels, "universes": len(unis),
            "outputs": len(patch.outputs), "errors": sum(i.level == IssueLevel.ERROR for i in issues),
            "warnings": sum(i.level == IssueLevel.WARNING for i in issues)}


ISSUE_COLUMNS = ["level", "code", "message", "fixture_ids", "output_id", "port_id", "universe", "channels"]


def issue_table(patch: Patch) -> Rows:
    rows: Rows = [ISSUE_COLUMNS[:]]
    for i in validate(patch):
        rows.append([i.level.value, i.code.value, i.message, "|".join(i.fixture_ids), i.output_id or "", i.port_id or "",
                     _s(i.universe), f"{i.channels[0]}-{i.channels[1]}" if i.channels else ""])
    return rows
