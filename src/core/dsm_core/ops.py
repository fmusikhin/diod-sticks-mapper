"""Операции правки патча. Каждая — чистая функция ``f(patch, …) -> Patch``:
входы проверяются до изменений, при ошибке бросается ``OperationError``, исходный патч нетронут.
Результат проходит проверку схемы.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from .errors import OperationError, SchemaError
from .ids import PREFIX_FIXTURE, PREFIX_FIXTURE_TYPE, PREFIX_GROUP, PREFIX_OUTPUT, PREFIX_PORT, new_ids
from .model import (Acceptance, Address, AddressMode, ArcShape, CurveShape, CustomShape, Direction, Fixture,
                    FixtureType, Group, LineShape, Output, Patch, PolylineShape, Port, RectShape, Shape, get_fixture,
                    get_fixture_type, get_group, get_output, get_port)
from .protocol import DMX_CHANNELS, universe_in_range, universe_range
from .schema import from_dict, to_dict


def _checked(patch: Patch) -> Patch:
    """Проверка результата по схеме: операции не должны производить невалидный патч."""
    try:
        return from_dict(to_dict(patch))
    except SchemaError as e:
        raise OperationError("результат операции не проходит схему: " + "; ".join(str(x) for x in e.errors)) from e


def _ids(items: Sequence[Any]) -> list[str]:
    return [it.id for it in items]


def _require_absent(ids: Sequence[str], new: str, what: str) -> None:
    if new in ids:
        raise OperationError(f"{what} с id {new!r} уже есть")


# ---------- профили ----------

def add_fixture_type(patch: Patch, ft: FixtureType) -> Patch:
    _require_absent(_ids(patch.fixture_types), ft.id, "профиль")
    return _checked(replace(patch, fixture_types=patch.fixture_types + (ft,)))


def update_fixture_type(patch: Patch, ft_id: str, **changes: Any) -> Patch:
    old = _get(get_fixture_type, patch, ft_id)
    if "id" in changes:
        raise OperationError("id профиля менять нельзя")
    new = replace(old, **changes)
    return _checked(replace(patch, fixture_types=tuple(new if t.id == ft_id else t for t in patch.fixture_types)))


def remove_fixture_type(patch: Patch, ft_id: str) -> Patch:
    _get(get_fixture_type, patch, ft_id)
    users = [f.id for f in patch.fixtures if f.type_id == ft_id]
    if users:
        raise OperationError(f"профиль {ft_id!r} используют фикстуры: {', '.join(users)}")
    return replace(patch, fixture_types=tuple(t for t in patch.fixture_types if t.id != ft_id))


# ---------- фикстуры ----------

def _get(getter, *args):
    try:
        return getter(*args)
    except KeyError as e:
        raise OperationError(str(e)) from e


def _detach(patch: Patch, fx_id: str) -> Patch:
    """Убрать фикстуру из списков выходов и портов (адрес не трогается)."""
    outs = []
    for o in patch.outputs:
        ports = tuple(replace(p, fixture_ids=tuple(i for i in p.fixture_ids if i != fx_id)) for p in o.ports)
        outs.append(replace(o, ports=ports, fixture_ids=tuple(i for i in o.fixture_ids if i != fx_id)))
    return replace(patch, outputs=tuple(outs))


def _attach(patch: Patch, fx: Fixture) -> Patch:
    """Поставить фикстуру в список выхода или порта по её адресу."""
    a = fx.address
    if a.output_id is None:
        return patch
    out = _get(get_output, patch, a.output_id)
    if a.port_id is not None:
        port = _get(get_port, out, a.port_id)
        port = replace(port, fixture_ids=port.fixture_ids + (fx.id,))
        out = replace(out, ports=tuple(port if p.id == port.id else p for p in out.ports))
    else:
        out = replace(out, fixture_ids=out.fixture_ids + (fx.id,))
    return replace(patch, outputs=tuple(out if o.id == out.id else o for o in patch.outputs))


def add_fixture(patch: Patch, fx: Fixture) -> Patch:
    _require_absent(_ids(patch.fixtures), fx.id, "фикстура")
    _get(get_fixture_type, patch, fx.type_id)
    p = replace(patch, fixtures=patch.fixtures + (fx,))
    p = _attach(p, fx)
    return _checked(p)


def update_fixture(patch: Patch, fx_id: str, **changes: Any) -> Patch:
    old = _get(get_fixture, patch, fx_id)
    if "id" in changes:
        raise OperationError("id фикстуры менять нельзя")
    new = replace(old, **changes)
    p = replace(patch, fixtures=tuple(new if f.id == fx_id else f for f in patch.fixtures))
    if new.address.output_id != old.address.output_id or new.address.port_id != old.address.port_id:
        p = _attach(_detach(p, fx_id), new)
    return _checked(p)


def remove_fixture(patch: Patch, fx_id: str) -> Patch:
    _get(get_fixture, patch, fx_id)
    p = _detach(patch, fx_id)
    groups = tuple(replace(g, fixture_ids=tuple(i for i in g.fixture_ids if i != fx_id)) for g in p.groups)
    return replace(p, groups=groups, fixtures=tuple(f for f in p.fixtures if f.id != fx_id))


def _shift(shape: Shape, dx: float, dy: float) -> Shape:
    def mv(p):
        return (p[0] + dx, p[1] + dy)
    if isinstance(shape, LineShape):
        return replace(shape, p1=mv(shape.p1), p2=mv(shape.p2))
    if isinstance(shape, (PolylineShape, CurveShape, CustomShape)):
        return replace(shape, points=tuple(mv(p) for p in shape.points))
    if isinstance(shape, (RectShape, ArcShape)):
        return replace(shape, center=mv(shape.center))
    return shape


def duplicate_fixtures(patch: Patch, fixture_ids: Sequence[str], dx: float, dy: float,
                       count: int = 1) -> tuple[Patch, list[str]]:
    """Копии с новыми id, тем же профилем, группами, выходом/портом (адрес сбрасывается в auto без номера).

    Возвращает патч и id копий в порядке: первая копия всех исходных, затем вторая и т. д.
    """
    if count < 1:
        raise OperationError("count должен быть ≥ 1")
    sources = [_get(get_fixture, patch, i) for i in fixture_ids]
    new = new_ids(_ids(patch.fixtures), PREFIX_FIXTURE, len(sources) * count)
    p = patch
    created: list[str] = []
    k = 0
    for c in range(1, count + 1):
        for src in sources:
            nid = new[k]
            k += 1
            addr = Address(mode=AddressMode.AUTO if src.address.output_id else AddressMode.UNASSIGNED,
                           output_id=src.address.output_id, port_id=src.address.port_id,
                           channel_offset=src.address.channel_offset)
            fx = replace(src, id=nid, name=f"{src.name} ({c})", shape=_shift(src.shape, dx * c, dy * c),
                         output_shape=_shift(src.output_shape, dx * c, dy * c) if src.output_shape else None,
                         address=addr, acceptance=Acceptance.NONE, extra=dict(src.extra))
            p = replace(p, fixtures=p.fixtures + (fx,))
            p = _attach(p, fx)
            groups = tuple(replace(g, fixture_ids=g.fixture_ids + (nid,)) if g.id in fx.groups else g for g in p.groups)
            p = replace(p, groups=groups)
            created.append(nid)
    return _checked(p), created


# ---------- адрес и приёмка ----------

def set_manual_address(patch: Patch, fx_id: str, output_id: str, universe: int, channel: int,
                       port_id: str | None = None) -> Patch:
    fx = _get(get_fixture, patch, fx_id)
    out = _get(get_output, patch, output_id)
    if port_id is not None:
        _get(get_port, out, port_id)
    if not universe_in_range(out.protocol, universe):
        lo, hi = universe_range(out.protocol)
        raise OperationError(f"юниверс {universe} вне диапазона протокола {lo}–{hi}")
    if not 1 <= channel <= DMX_CHANNELS:
        raise OperationError(f"канал {channel} вне диапазона 1–{DMX_CHANNELS}")
    addr = Address(mode=AddressMode.MANUAL, output_id=output_id, port_id=port_id, universe=universe,
                   channel=channel, channel_offset=fx.address.channel_offset)
    return update_fixture(patch, fx_id, address=addr)


def assign_to_output(patch: Patch, fx_id: str, output_id: str | None, port_id: str | None = None) -> Patch:
    """Перевести фикстуру на выход/порт с режимом auto без номера (или снять с выхода при None)."""
    fx = _get(get_fixture, patch, fx_id)
    if output_id is None:
        addr = Address(channel_offset=fx.address.channel_offset)
    else:
        out = _get(get_output, patch, output_id)
        if port_id is not None:
            _get(get_port, out, port_id)
        addr = Address(mode=AddressMode.AUTO, output_id=output_id, port_id=port_id,
                       channel_offset=fx.address.channel_offset)
    return update_fixture(patch, fx_id, address=addr)


def clear_address(patch: Patch, fx_id: str) -> Patch:
    return assign_to_output(patch, fx_id, None)


def set_acceptance(patch: Patch, fx_id: str, status: Acceptance) -> Patch:
    return update_fixture(patch, fx_id, acceptance=status)


def fix_reversed(patch: Patch, fx_id: str) -> Patch:
    fx = _get(get_fixture, patch, fx_id)
    d = Direction.REVERSE if fx.direction == Direction.FORWARD else Direction.FORWARD
    return update_fixture(patch, fx_id, direction=d, acceptance=Acceptance.NONE)


# ---------- выходы и порты ----------

def add_output(patch: Patch, out: Output) -> Patch:
    _require_absent(_ids(patch.outputs), out.id, "выход")
    return _checked(replace(patch, outputs=patch.outputs + (out,)))


def update_output(patch: Patch, out_id: str, **changes: Any) -> Patch:
    old = _get(get_output, patch, out_id)
    if "id" in changes:
        raise OperationError("id выхода менять нельзя")
    return _checked(replace(patch, outputs=tuple(replace(old, **changes) if o.id == out_id else o for o in patch.outputs)))


def remove_output(patch: Patch, out_id: str) -> Patch:
    _get(get_output, patch, out_id)
    users = [f.id for f in patch.fixtures if f.address.output_id == out_id]
    if users:
        raise OperationError(f"на выход {out_id!r} адресованы фикстуры: {', '.join(users)} — сначала снимите адрес")
    return replace(patch, outputs=tuple(o for o in patch.outputs if o.id != out_id))


def add_port(patch: Patch, out_id: str, port: Port) -> Patch:
    out = _get(get_output, patch, out_id)
    _require_absent(_ids(out.ports), port.id, "порт")
    return update_output(patch, out_id, ports=out.ports + (port,))


def update_port(patch: Patch, out_id: str, port_id: str, **changes: Any) -> Patch:
    out = _get(get_output, patch, out_id)
    old = _get(get_port, out, port_id)
    if "id" in changes:
        raise OperationError("id порта менять нельзя")
    return update_output(patch, out_id, ports=tuple(replace(old, **changes) if p.id == port_id else p for p in out.ports))


def remove_port(patch: Patch, out_id: str, port_id: str) -> Patch:
    out = _get(get_output, patch, out_id)
    port = _get(get_port, out, port_id)
    if port.fixture_ids:
        raise OperationError(f"в порту {port_id!r} есть фикстуры: {', '.join(port.fixture_ids)}")
    return update_output(patch, out_id, ports=tuple(p for p in out.ports if p.id != port_id))


# ---------- группы ----------

def add_group(patch: Patch, group: Group) -> Patch:
    _require_absent(_ids(patch.groups), group.id, "группа")
    p = replace(patch, groups=patch.groups + (group,))
    p = _sync_group_membership(p, group)
    return _checked(p)


def update_group(patch: Patch, grp_id: str, **changes: Any) -> Patch:
    old = _get(get_group, patch, grp_id)
    if "id" in changes:
        raise OperationError("id группы менять нельзя")
    new = replace(old, **changes)
    p = replace(patch, groups=tuple(new if g.id == grp_id else g for g in patch.groups))
    return _checked(_sync_group_membership(p, new))


def set_group_members(patch: Patch, grp_id: str, fixture_ids: Sequence[str]) -> Patch:
    return update_group(patch, grp_id, fixture_ids=tuple(fixture_ids))


def remove_group(patch: Patch, grp_id: str) -> Patch:
    _get(get_group, patch, grp_id)
    fixtures = tuple(replace(f, groups=tuple(g for g in f.groups if g != grp_id)) for f in patch.fixtures)
    return replace(patch, fixtures=fixtures, groups=tuple(g for g in patch.groups if g.id != grp_id))


def _sync_group_membership(patch: Patch, group: Group) -> Patch:
    """Поле ``Fixture.groups`` следует за ``Group.fixture_ids``."""
    members = set(group.fixture_ids)
    fixtures = []
    for f in patch.fixtures:
        in_list = group.id in f.groups
        if f.id in members and not in_list:
            f = replace(f, groups=f.groups + (group.id,))
        elif f.id not in members and in_list:
            f = replace(f, groups=tuple(g for g in f.groups if g != group.id))
        fixtures.append(f)
    return replace(patch, fixtures=tuple(fixtures))


# ---------- id-помощники для потребителей ----------

def next_ids(patch: Patch, kind: str, count: int = 1) -> list[str]:
    prefix = {"fixture_type": PREFIX_FIXTURE_TYPE, "fixture": PREFIX_FIXTURE, "output": PREFIX_OUTPUT,
              "port": PREFIX_PORT, "group": PREFIX_GROUP}[kind]
    existing = {"fixture_type": _ids(patch.fixture_types), "fixture": _ids(patch.fixtures),
                "output": _ids(patch.outputs), "port": [p.id for o in patch.outputs for p in o.ports],
                "group": _ids(patch.groups)}[kind]
    return new_ids(existing, prefix, count)
