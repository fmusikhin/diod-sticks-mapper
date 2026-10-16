"""Фабрики патчей для тестов. Все IP — только 127.0.0.x и 192.0.2.x (TEST-NET)."""
import pytest

from dsm_core.model import (Address, AddressMode, Canvas, Fixture, FixtureKind, FixtureType, LineShape,
                            Output, Patch, Port, TargetKind)
from dsm_core.protocol import Protocol
from dsm_core.schema import SCHEMA_VERSION


def rgb_type(ft_id="ft1", cells=144, order="RGB", **kw):
    return FixtureType(id=ft_id, name=f"{order} {cells}", kind=FixtureKind.STICK, cells=cells,
                       color_order=order, **kw)


def line(x, n=0):
    return LineShape(p1=(x, 0.0), p2=(x, 100.0 + n))


def fixture(fx_id, type_id="ft1", output_id="out1", x=0.0, **kw):
    addr = kw.pop("address", Address(mode=AddressMode.AUTO, output_id=output_id))
    return Fixture(id=fx_id, name=fx_id, type_id=type_id, shape=line(x), address=addr, **kw)


def output(out_id="out1", protocol=Protocol.ARTNET, start=0, ip=None, fixture_ids=(), ports=(),
           target_kind=TargetKind.UNICAST, **kw):
    return Output(id=out_id, name=out_id, protocol=protocol, start_universe=start, ip=ip,
                  target_kind=target_kind, fixture_ids=tuple(fixture_ids), ports=tuple(ports), **kw)


def empty_like(p):
    """Патч с теми же профилями, выходами (пустые списки, порты без фикстур) и группами без членов."""
    from dataclasses import replace
    outs = tuple(replace(o, fixture_ids=(), ports=tuple(replace(pt, fixture_ids=()) for pt in o.ports)) for o in p.outputs)
    groups = tuple(replace(g, fixture_ids=()) for g in p.groups)
    return make_patch(p.fixture_types, (), outs, groups)


def make_patch(fixture_types=(), fixtures=(), outputs=(), groups=()):
    return Patch(schema_version=SCHEMA_VERSION, canvas=Canvas(), fixture_types=tuple(fixture_types),
                 fixtures=tuple(fixtures), outputs=tuple(outputs), groups=tuple(groups))


@pytest.fixture
def sticks_patch():
    """8 стиков 144 px RGB на одном выходе Art-Net с двумя портами по 4."""
    fxs = [fixture(f"fx{i}", x=float(i * 10)) for i in range(1, 9)]
    ports = (Port(id="port1", number=1, fixture_ids=("fx1", "fx2", "fx3", "fx4")),
             Port(id="port2", number=2, fixture_ids=("fx5", "fx6", "fx7", "fx8")))
    return make_patch([rgb_type()], fxs, [output(ports=ports)])


@pytest.fixture
def stage0_patch():
    """Стенд этапа 0: 200 / 60 / 128 px RGB на 127.0.0.1/2/3, стартовый юниверс 20 Art-Net."""
    types = [rgb_type("ft200", 200), rgb_type("ft60", 60), rgb_type("ft128", 128)]
    fxs = [fixture("fx1", "ft200", "out1"), fixture("fx2", "ft60", "out2", x=10.0),
           fixture("fx3", "ft128", "out3", x=20.0)]
    outs = [output("out1", start=20, ip="127.0.0.1", fixture_ids=("fx1",)),
            output("out2", start=20, ip="127.0.0.2", fixture_ids=("fx2",)),
            output("out3", start=20, ip="127.0.0.3", fixture_ids=("fx3",))]
    return make_patch(types, fxs, outs)
