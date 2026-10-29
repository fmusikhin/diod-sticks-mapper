"""Проверки конфликтов патча. Не бросает исключений и не меняет патч: конфликты — свойство документа."""
from __future__ import annotations

from .addressing import auto_assign_all, layout_fixture
from .cells import cell_count
from .errors import LayoutError
from .model import (AddressMode, ArcShape, Fixture, LineShape, Output, Patch, PolylineShape, RectShape,
                    TargetKind, effective_target_ip)
from .occupancy import Occupancy
from .protocol import DMX_CHANNELS, Protocol, sacn_multicast, universe_in_range, universe_range
from .validate_types import Issue, IssueCode, IssueLevel

E, W = IssueLevel.ERROR, IssueLevel.WARNING


def _degenerate(fx: Fixture) -> bool:
    s = fx.shape
    if isinstance(s, LineShape):
        return s.p1 == s.p2
    if isinstance(s, RectShape):
        return s.size[0] <= 0 or s.size[1] <= 0
    if isinstance(s, PolylineShape):
        return len(set(s.points)) < 2
    if isinstance(s, ArcShape):
        return s.radius <= 0 or s.start_deg == s.end_deg
    return False


def _naive_straddle(ft, fx: Fixture, start_channel: int) -> int | None:
    """Индекс первой ячейки, которая при сплошной раскладке от start_channel легла бы на границу юниверса."""
    cpc = ft.channels_per_cell
    if cpc <= 1:
        return None
    c0 = start_channel + ft.head.width
    for k in range(cell_count(ft, fx)):
        a = c0 + k * cpc
        b = a + cpc - 1
        if (a - 1) // DMX_CHANNELS != (b - 1) // DMX_CHANNELS:
            return k
        if b > start_channel + DMX_CHANNELS * 64:
            break
    return None


def _target_key(out: Output, universe: int) -> tuple:
    if out.protocol == Protocol.SACN and out.target_kind == TargetKind.MULTICAST and not out.ip:
        return ("sacn", sacn_multicast(universe) if universe_in_range(Protocol.SACN, universe) else universe, universe)
    return (out.protocol.value, effective_target_ip(out), universe)


def validate(patch: Patch) -> list[Issue]:
    """Все конфликты патча: ошибки раньше предупреждений, затем по выходу и юниверсу. Патч не меняется."""
    issues: list[Issue] = []
    types = {t.id: t for t in patch.fixture_types}
    outs = {o.id: o for o in patch.outputs}
    groups = {g.id for g in patch.groups}
    fx_ids = {f.id for f in patch.fixtures}

    # --- выходы ---
    for out in patch.outputs:
        lo, hi = universe_range(out.protocol)
        if not lo <= out.start_universe <= hi:
            issues.append(Issue(E, IssueCode.UNIVERSE_RANGE,
                                f"выход {out.name!r}: стартовый юниверс {out.start_universe} вне диапазона {lo}–{hi}",
                                (), out.id, None, out.start_universe))
        if out.universe_count and out.start_universe + out.universe_count - 1 > hi:
            issues.append(Issue(E, IssueCode.UNIVERSE_RANGE,
                                f"выход {out.name!r}: последний юниверс {out.start_universe + out.universe_count - 1} вне диапазона {lo}–{hi}",
                                (), out.id))
        if out.protocol == Protocol.ARTNET and out.target_kind == TargetKind.MULTICAST:
            issues.append(Issue(E, IssueCode.PROTOCOL_MISMATCH, f"выход {out.name!r}: multicast недоступен для Art-Net", (), out.id))
        if out.protocol == Protocol.SACN and out.target_kind == TargetKind.BROADCAST:
            issues.append(Issue(E, IssueCode.PROTOCOL_MISMATCH, f"выход {out.name!r}: broadcast недоступен для sACN", (), out.id))
        if not out.ip and not (out.protocol == Protocol.SACN and out.target_kind == TargetKind.MULTICAST):
            issues.append(Issue(W, IssueCode.TARGET_MISSING, f"выход {out.name!r}: цель не задана, вывод на 127.0.0.1", (), out.id))
        for p in out.ports:
            for fid in p.fixture_ids:
                if fid not in fx_ids:
                    issues.append(Issue(E, IssueCode.BROKEN_REF, f"порт {p.id!r} выхода {out.name!r}: фикстура {fid!r} не найдена", (), out.id, p.id))
        for fid in out.fixture_ids:
            if fid not in fx_ids:
                issues.append(Issue(E, IssueCode.BROKEN_REF, f"выход {out.name!r}: фикстура {fid!r} не найдена", (), out.id))

    # --- фикстуры: ссылки, формы, адреса, сегменты ---
    occ = Occupancy()
    used: dict[str, set[int]] = {o.id: set() for o in patch.outputs}
    port_cells: dict[tuple[str, str], int] = {}
    fx_load: dict[str, tuple[int, set[int]]] = {}        # фикстура порта → (ячеек, юниверсы) — кто не влез в лимит
    port_unis: dict[tuple[str, str], set[int]] = {}
    for fx in patch.fixtures:
        ft = types.get(fx.type_id)
        if ft is None:
            issues.append(Issue(E, IssueCode.BROKEN_REF, f"фикстура {fx.name!r}: профиль {fx.type_id!r} не найден", (fx.id,)))
        for g in fx.groups:
            if g not in groups:
                issues.append(Issue(E, IssueCode.BROKEN_REF, f"фикстура {fx.name!r}: группа {g!r} не найдена", (fx.id,)))
        if _degenerate(fx):
            issues.append(Issue(W, IssueCode.DEGENERATE_SHAPE, f"фикстура {fx.name!r}: вырожденная форма", (fx.id,)))
        a = fx.address
        if a.output_id is None or a.universe is None or a.channel is None:
            issues.append(Issue(W, IssueCode.UNASSIGNED, f"фикстура {fx.name!r}: нет адреса", (fx.id,), a.output_id, a.port_id))
            continue
        out = outs.get(a.output_id)
        if out is None:
            issues.append(Issue(E, IssueCode.BROKEN_REF, f"фикстура {fx.name!r}: выход {a.output_id!r} не найден", (fx.id,)))
            continue
        if a.port_id is not None and all(p.id != a.port_id for p in out.ports):
            issues.append(Issue(E, IssueCode.BROKEN_REF, f"фикстура {fx.name!r}: порт {a.port_id!r} не найден", (fx.id,), out.id))
        if not universe_in_range(out.protocol, a.universe):
            lo, hi = universe_range(out.protocol)
            issues.append(Issue(E, IssueCode.UNIVERSE_RANGE, f"фикстура {fx.name!r}: юниверс {a.universe} вне диапазона {lo}–{hi}",
                                (fx.id,), out.id, a.port_id, a.universe))
            continue
        if ft is None:
            continue
        try:
            segs = layout_fixture(ft, fx, out, a.universe, a.channel)
        except LayoutError as e:
            code = IssueCode.OVER_512 if (a.mode == AddressMode.MANUAL and not out.rules.auto_span) else IssueCode.FIXTURE_TOO_LONG
            issues.append(Issue(E, code, f"фикстура {fx.name!r}: {e}", (fx.id,), out.id, a.port_id, a.universe))
            continue
        if a.mode == AddressMode.MANUAL:
            k = _naive_straddle(ft, fx, a.channel)
            if k is not None:
                moved = next((s for s in segs if s.kind == "cells" and s.first_cell <= k < s.first_cell + s.n_cells), None)
                if out.rules.whole_pixel:
                    where = f"движок перенесёт её в юниверс {moved.universe}, канал {moved.first_channel + (k - moved.first_cell) * ft.channels_per_cell}" if moved else ""
                    issues.append(Issue(W, IssueCode.CELL_ON_BOUNDARY,
                                        f"фикстура {fx.name!r}: ячейка {k} ложится на границу юниверса; {where}",
                                        (fx.id,), out.id, a.port_id, a.universe))
                else:
                    issues.append(Issue(E, IssueCode.CELL_ON_BOUNDARY,
                                        f"фикстура {fx.name!r}: ячейка {k} разрезана границей юниверса (whole_pixel выключен)",
                                        (fx.id,), out.id, a.port_id, a.universe))
        for s in segs:
            occ.add(out.id, s.universe, s.first_channel, s.last_channel, fx.id)
            used[out.id].add(s.universe)
        if a.port_id is not None:
            key = (out.id, a.port_id)
            port_cells[key] = port_cells.get(key, 0) + cell_count(ft, fx)
            port_unis.setdefault(key, set()).update(s.universe for s in segs)
            fx_load[fx.id] = (cell_count(ft, fx), {s.universe for s in segs})

    # --- пересечения каналов ---
    names = {f.id: f.name for f in patch.fixtures}
    for out_id, u, (f1, l1, a_id), (f2, l2, b_id) in occ.overlaps():
        lo, hi = max(f1, f2), min(l1, l2)
        issues.append(Issue(E, IssueCode.CHANNEL_OVERLAP,
                            f"пересечение каналов {lo}–{hi} в юниверсе {u} выхода {outs[out_id].name!r}: "
                            f"{names.get(a_id, a_id)!r} и {names.get(b_id, b_id)!r}",
                            (a_id, b_id), out_id, None, u, (lo, hi)))

    # --- лимиты портов: в ошибке — только фикстуры, не влезшие в лимит по порядку цепочки (первые выводятся) ---
    for out in patch.outputs:
        for p in out.ports:
            key = (out.id, p.id)
            n = port_cells.get(key, 0)
            if p.max_cells is not None and n > p.max_cells:
                over, acc = [], 0
                for fid in p.fixture_ids:
                    acc += fx_load.get(fid, (0, set()))[0]
                    if acc > p.max_cells:
                        over.append(fid)
                issues.append(Issue(E, IssueCode.PORT_LIMIT, f"порт {p.number} выхода {out.name!r}: {n} ячеек при лимите {p.max_cells}",
                                    tuple(over), out.id, p.id))
            un = len(port_unis.get(key, ()))
            if p.max_universes is not None and un > p.max_universes:
                over, unis = [], set()
                for fid in p.fixture_ids:
                    unis |= fx_load.get(fid, (0, set()))[1]
                    if len(unis) > p.max_universes:
                        over.append(fid)
                issues.append(Issue(E, IssueCode.PORT_LIMIT, f"порт {p.number} выхода {out.name!r}: {un} юниверсов при лимите {p.max_universes}",
                                    tuple(over), out.id, p.id))

    # --- дубли юниверсов на одной цели ---
    seen: dict[tuple, str] = {}
    for out in patch.outputs:
        unis = set(used[out.id])
        if out.universe_count:
            unis |= set(range(out.start_universe, out.start_universe + out.universe_count))
        for u in sorted(unis):
            key = _target_key(out, u)
            other = seen.get(key)
            if other is not None and other != out.id:
                issues.append(Issue(E, IssueCode.UNIVERSE_DUP,
                                    f"юниверс {u} на цели {key[1]} задан и у выхода {outs[other].name!r}, и у {out.name!r}",
                                    (), out.id, None, u))
            else:
                seen[key] = out.id

    # --- устаревшие авто-адреса ---
    try:
        fresh, _ = auto_assign_all(patch)
    except Exception:  # битые ссылки уже отмечены выше
        fresh = patch
    if fresh is not patch:
        for old, new in zip(patch.fixtures, fresh.fixtures):
            if (old.address.mode == AddressMode.AUTO and old.address != new.address
                    and new.address.universe is not None):
                issues.append(Issue(W, IssueCode.STALE_AUTO,
                                    f"фикстура {old.name!r}: авто-адрес {old.address.universe}:{old.address.channel} устарел, "
                                    f"пересчёт даст {new.address.universe}:{new.address.channel}",
                                    (old.id,), old.address.output_id, old.address.port_id, old.address.universe))

    issues.sort(key=lambda i: (i.level != E, i.output_id or "", i.universe if i.universe is not None else -1,
                               i.fixture_ids[0] if i.fixture_ids else ""))
    return issues
