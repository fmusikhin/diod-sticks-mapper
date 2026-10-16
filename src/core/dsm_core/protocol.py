"""Нумерация юниверсов Art-Net 4 и sACN.

Art-Net: port-address 0–32767 = Net(7 бит) << 8 | Sub-Net(4 бита) << 4 | Universe(4 бита).
Подтверждено этапом 0: юниверс 20 уходит как subnet 1 / universe 4 (net 0).
sACN (E1.31): юниверс 1–63999, multicast-группа 239.255.hi.lo.
"""
from __future__ import annotations

from enum import Enum


class Protocol(str, Enum):
    ARTNET = "artnet"
    SACN = "sacn"


ARTNET_MAX = 32767
SACN_MIN = 1
SACN_MAX = 63999
DMX_CHANNELS = 512


def to_port_address(net: int, subnet: int, universe: int) -> int:
    if not (0 <= net <= 127 and 0 <= subnet <= 15 and 0 <= universe <= 15):
        raise ValueError(f"net.subnet.universe вне диапазона: {net}.{subnet}.{universe}")
    return (net << 8) | (subnet << 4) | universe


def from_port_address(pa: int) -> tuple[int, int, int]:
    if not 0 <= pa <= ARTNET_MAX:
        raise ValueError(f"port-address вне диапазона 0–{ARTNET_MAX}: {pa}")
    return (pa >> 8) & 0x7F, (pa >> 4) & 0xF, pa & 0xF


def universe_range(protocol: Protocol) -> tuple[int, int]:
    return (0, ARTNET_MAX) if protocol == Protocol.ARTNET else (SACN_MIN, SACN_MAX)


def universe_in_range(protocol: Protocol, universe: int) -> bool:
    lo, hi = universe_range(protocol)
    return lo <= universe <= hi


def format_universe(protocol: Protocol, universe: int) -> str:
    """Сквозной номер; для Art-Net — ещё и net.subnet.universe: ``"20 (0.1.4)"``."""
    if protocol == Protocol.ARTNET and 0 <= universe <= ARTNET_MAX:
        return f"{universe} ({dotted(universe)})"
    return str(universe)


def dotted(port_address: int) -> str:
    n, s, u = from_port_address(port_address)
    return f"{n}.{s}.{u}"


def sacn_multicast(universe: int) -> str:
    if not SACN_MIN <= universe <= SACN_MAX:
        raise ValueError(f"юниверс sACN вне диапазона {SACN_MIN}–{SACN_MAX}: {universe}")
    return f"239.255.{universe >> 8}.{universe & 0xFF}"
