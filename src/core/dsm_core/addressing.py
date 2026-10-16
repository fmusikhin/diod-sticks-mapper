"""Раскладка фикстуры на юниверсы и автоадресация по правилам выхода и порта.

Сегмент — непрерывный блок каналов одного юниверса. Для ``pixel-engine`` сегмент = примитив
с ``DMXFixtureUniverse`` / ``DMXFixtureChannel``. При правилах по умолчанию сегменты совпадают
с тем, как ``dmxfixturePOP`` сам раскладывает фикстуру (этап 0): ячейка целиком в следующий юниверс.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal, Sequence

from .cells import cell_count
from .errors import LayoutError, OperationError
from .model import (Address, AddressMode, Fixture, FixtureType, Output, Patch, get_fixture, get_fixture_type,
                    get_output)
from .occupancy import Occupancy
from .protocol import DMX_CHANNELS, universe_range
from .validate_types import Issue, IssueCode, IssueLevel


@dataclass(frozen=True, slots=True)
class Segment:
    universe: int
    first_channel: int
    last_channel: int
    kind: Literal["head", "cells", "tail"]
    first_cell: int = 0
    n_cells: int = 0
    partial: bool = False     # ячейка разрезана между сегментами (whole_pixel = false)


def channel_limit(output: Output) -> int:
    return 510 if output.rules.skip_511_512 else DMX_CHANNELS


def _last_universe(output: Output) -> int:
    hi = universe_range(output.protocol)[1]
    if output.universe_count is not None:
        hi = min(hi, output.start_universe + output.universe_count - 1)
    return hi


class _Cursor:
    def __init__(self, output: Output, universe: int, channel: int, occ: Occupancy | None, fx_id: str) -> None:
        self.out = output
        self.u = universe
        self.ch = channel
        self.occ = occ
        self.limit = channel_limit(output)
        self.last_u = _last_universe(output)
        self.fx_id = fx_id

    def next_universe(self) -> None:
        if not self.out.rules.auto_span:
            raise LayoutError(f"фикстура {self.fx_id!r} не помещается в юниверс {self.u} (auto_span выключен)")
        if self.u + 1 > self.last_u:
            raise LayoutError(f"фикстура {self.fx_id!r}: кончились юниверсы выхода {self.out.id!r} после {self.u}")
        self.u += 1
        self.ch = 1

    def run(self) -> tuple[int, int]:
        """(позиция, длина) первого свободного участка от курсора; при нужде — следующий юниверс."""
        while True:
            if self.occ is not None:
                fr = self.occ.free_run(self.out.id, self.u, self.ch, self.limit)
            else:
                fr = (self.ch, self.limit - self.ch + 1) if self.ch <= self.limit else None
            if fr is not None and fr[1] >= 1:
                return fr
            self.next_universe()

    def place_block(self, width: int) -> tuple[int, int]:
        """Неделимый блок width каналов → (юниверс, первый канал)."""
        while True:
            pos, length = self.run()
            if length >= width:
                self.ch = pos + width
                return self.u, pos
            # не влезает здесь: искать дальше в этом юниверсе, потом в следующем
            self.ch = pos + length
            if self.ch > self.limit:
                self.next_universe()


def _merge_cells(segs: list[Segment]) -> list[Segment]:
    out: list[Segment] = []
    for s in segs:
        if (out and s.kind == "cells" and out[-1].kind == "cells" and out[-1].universe == s.universe
                and out[-1].last_channel + 1 == s.first_channel and not s.partial and not out[-1].partial):
            prev = out[-1]
            out[-1] = replace(prev, last_channel=s.last_channel, n_cells=prev.n_cells + s.n_cells)
        else:
            out.append(s)
    return out


def layout_fixture(ft: FixtureType, fx: Fixture, output: Output, universe: int, channel: int) -> list[Segment]:
    """Сегменты фикстуры от (universe, channel) по правилам выхода. ``LayoutError`` — не помещается.

    Раскладка зависит только от стартового адреса и правил: фикстуру делит лишь граница юниверса,
    поэтому движок, зная (юниверс, канал), получит те же сегменты.
    """
    if not 1 <= channel <= DMX_CHANNELS:
        raise LayoutError(f"канал {channel} вне диапазона 1–{DMX_CHANNELS}")
    cur = _Cursor(output, universe, channel, None, fx.id)
    segs: list[Segment] = []
    if ft.head.width:
        u, pos = cur.place_block(ft.head.width)
        segs.append(Segment(u, pos, pos + ft.head.width - 1, "head"))
    n = cell_count(ft, fx)
    cpc = ft.channels_per_cell
    if n and cpc:
        if output.rules.whole_pixel:
            placed = 0
            while placed < n:
                pos, length = cur.run()
                k = min(length // cpc, n - placed)
                if k == 0:
                    cur.ch = pos + length
                    if cur.ch > cur.limit:
                        cur.next_universe()
                    continue
                segs.append(Segment(cur.u, pos, pos + k * cpc - 1, "cells", placed, k))
                placed += k
                cur.ch = pos + k * cpc
        else:
            total = n * cpc
            done = 0
            while done < total:
                pos, length = cur.run()
                take = min(length, total - done)
                s0, s1 = done, done + take - 1
                first_cell = s0 // cpc
                n_cells = (s1 // cpc) - first_cell + 1
                partial = (s0 % cpc != 0) or ((s1 + 1) % cpc != 0)
                segs.append(Segment(cur.u, pos, pos + take - 1, "cells", first_cell, n_cells, partial))
                done += take
                cur.ch = pos + take
    if ft.tail.width:
        u, pos = cur.place_block(ft.tail.width)
        segs.append(Segment(u, pos, pos + ft.tail.width - 1, "tail"))
    return _merge_cells(segs)


def fixture_segments(patch: Patch, fx: Fixture) -> list[Segment]:
    """Сегменты фикстуры по её текущему адресу; пусто, если адреса нет."""
    a = fx.address
    if a.output_id is None or a.universe is None or a.channel is None:
        return []
    return layout_fixture(get_fixture_type(patch, fx.type_id), fx, get_output(patch, a.output_id),
                          a.universe, a.channel)


def first_conflict(occ: Occupancy, out_id: str, segs: Sequence[Segment]) -> tuple[int, int, int, str] | None:
    """Первое пересечение сегментов с занятым: (юниверс, first, last, fixture_id) занятого интервала."""
    for s in segs:
        for first, last, fid in occ.intervals(out_id, s.universe):
            if first > s.last_channel:
                break
            if last >= s.first_channel:
                return (s.universe, first, last, fid)
    return None


def layout_avoiding(ft: FixtureType, fx: Fixture, output: Output, universe: int, channel: int,
                    occ: Occupancy) -> list[Segment]:
    """Раскладка от первого места, где фикстура не пересекается с занятым: препятствие сдвигает начало."""
    limit = channel_limit(output)
    u, ch = universe, channel
    for _ in range(10000):
        if u > _last_universe(output):
            raise LayoutError(f"фикстура {fx.id!r}: кончились юниверсы выхода {output.id!r}")
        if ch > limit:
            u, ch = u + 1, 1
            continue
        segs = layout_fixture(ft, fx, output, u, ch)
        c = first_conflict(occ, output.id, segs)
        if c is None:
            return segs
        cu, _, last, _ = c
        u, ch = cu, last + 1
    raise LayoutError(f"фикстура {fx.id!r}: не найдено свободное место")


def _ordered_fixtures(out: Output) -> list[tuple[str, str | None, bool]]:
    """(fixture_id, port_id, первая в порту) в порядке обхода: порты по номеру, затем список выхода."""
    seq: list[tuple[str, str | None, bool]] = []
    for p in sorted(out.ports, key=lambda p: p.number):
        for i, fid in enumerate(p.fixture_ids):
            seq.append((fid, p.id, i == 0))
    for fid in out.fixture_ids:
        seq.append((fid, None, False))
    return seq


def _occupy(occ: Occupancy, out_id: str, fx_id: str, segs: Sequence[Segment]) -> None:
    for s in segs:
        occ.add(out_id, s.universe, s.first_channel, s.last_channel, fx_id)


def _manual_occupancy(patch: Patch, out: Output, skip: set[str] = frozenset()) -> tuple[Occupancy, list[Issue]]:
    occ = Occupancy()
    issues: list[Issue] = []
    for fx in patch.fixtures:
        if fx.address.output_id != out.id or fx.id in skip or fx.address.mode != AddressMode.MANUAL:
            continue
        try:
            segs = fixture_segments(patch, fx)
        except LayoutError as e:
            issues.append(Issue(IssueLevel.ERROR, IssueCode.FIXTURE_TOO_LONG, str(e), (fx.id,), out.id,
                                fx.address.port_id, fx.address.universe))
            continue
        _occupy(occ, out.id, fx.id, segs)
    return occ, issues


def _flow(patch: Patch, out: Output, seq: list[tuple[str, str | None, bool]], occ: Occupancy,
          universe: int, channel: int, first_manual: bool = False) -> tuple[dict[str, Address], list[Issue]]:
    """Раздать адреса фикстурам seq подряд от (universe, channel), обходя занятое в occ."""
    limit = channel_limit(out)
    u, ch = universe, channel
    addrs: dict[str, Address] = {}
    issues: list[Issue] = []
    ports = {p.id: p for p in out.ports}
    first = True
    for fid, port_id, first_in_port in seq:
        fx = get_fixture(patch, fid)
        ft = get_fixture_type(patch, fx.type_id)
        if first_in_port and ports[port_id].new_universe and ch != 1:
            u, ch = u + 1, 1
        ch += fx.address.channel_offset
        if ch > limit:
            u, ch = u + 1, 1
        try:
            segs = layout_avoiding(ft, fx, out, u, ch, occ)
        except LayoutError as e:
            issues.append(Issue(IssueLevel.ERROR, IssueCode.FIXTURE_TOO_LONG, str(e), (fid,), out.id, port_id, u))
            addrs[fid] = Address(mode=AddressMode.AUTO, output_id=out.id, port_id=port_id,
                                 channel_offset=fx.address.channel_offset)
            continue
        _occupy(occ, out.id, fid, segs)
        mode = AddressMode.MANUAL if (first and first_manual) else AddressMode.AUTO
        addrs[fid] = Address(mode=mode, output_id=out.id, port_id=port_id, universe=segs[0].universe,
                             channel=segs[0].first_channel, channel_offset=fx.address.channel_offset)
        u, ch = segs[-1].universe, segs[-1].last_channel + 1
        if ch > limit:
            u, ch = u + 1, 1
        first = False
    return addrs, issues


def _apply(patch: Patch, addrs: dict[str, Address]) -> Patch:
    if not addrs:
        return patch
    return replace(patch, fixtures=tuple(replace(f, address=addrs[f.id]) if f.id in addrs else f for f in patch.fixtures))


def auto_assign(patch: Patch, output_id: str) -> tuple[Patch, list[Issue]]:
    """Автоадресация выхода: ручные адреса не трогаются и обходятся; результат пишется в патч."""
    try:
        out = get_output(patch, output_id)
    except KeyError as e:
        raise OperationError(str(e)) from e
    occ, issues = _manual_occupancy(patch, out)
    seq = [(fid, pid, first) for fid, pid, first in _ordered_fixtures(out)
           if get_fixture(patch, fid).address.mode != AddressMode.MANUAL]
    addrs, more = _flow(patch, out, seq, occ, out.start_universe, 1)
    return _apply(patch, addrs), issues + more


def auto_assign_all(patch: Patch) -> tuple[Patch, list[Issue]]:
    issues: list[Issue] = []
    for out in patch.outputs:
        patch, more = auto_assign(patch, out.id)
        issues += more
    return patch, issues


def assign_sequential(patch: Patch, fixture_ids: Sequence[str], universe: int,
                      channel: int) -> tuple[Patch, list[Issue]]:
    """Раздать выбранным фикстурам адреса подряд от (universe, channel): первая — manual, остальные — auto.

    Дубль группы блоком и Ctrl+D. Все фикстуры должны быть на одном выходе; занятое другими обходится.
    """
    if not fixture_ids:
        return patch, []
    fxs = [get_fixture(patch, i) for i in fixture_ids]
    out_ids = {f.address.output_id for f in fxs}
    if len(out_ids) != 1 or None in out_ids:
        raise OperationError("фикстуры должны быть адресованы на один выход")
    out = get_output(patch, next(iter(out_ids)))
    lo, hi = universe_range(out.protocol)
    if not lo <= universe <= hi or not 1 <= channel <= DMX_CHANNELS:
        raise OperationError(f"адрес {universe}:{channel} вне диапазона")
    occ = Occupancy()
    chosen = set(fixture_ids)
    for fx in patch.fixtures:
        if fx.address.output_id == out.id and fx.id not in chosen:
            try:
                _occupy(occ, out.id, fx.id, fixture_segments(patch, fx))
            except LayoutError:
                pass
    seq = [(f.id, f.address.port_id, False) for f in fxs]
    addrs, issues = _flow(patch, out, seq, occ, universe, channel, first_manual=True)
    return _apply(patch, addrs), issues


def segments_by_fixture(patch: Patch) -> dict[str, list[Segment]]:
    """Сегменты всех адресованных фикстур (контракт с pixel-engine). Непомещающиеся — пустой список."""
    res: dict[str, list[Segment]] = {}
    for fx in patch.fixtures:
        try:
            res[fx.id] = fixture_segments(patch, fx)
        except LayoutError:
            res[fx.id] = []
    return res
