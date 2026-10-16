"""Типы сообщений проверки (вынесены отдельно: нужны и адресации, и проверкам)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class IssueLevel(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class IssueCode(str, Enum):
    CHANNEL_OVERLAP = "channel_overlap"
    OVER_512 = "over_512"
    CELL_ON_BOUNDARY = "cell_on_boundary"
    PORT_LIMIT = "port_limit"
    UNIVERSE_DUP = "universe_dup"
    UNIVERSE_RANGE = "universe_range"
    BROKEN_REF = "broken_ref"
    TARGET_MISSING = "target_missing"
    UNASSIGNED = "unassigned"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    DEGENERATE_SHAPE = "degenerate_shape"
    FIXTURE_TOO_LONG = "fixture_too_long"
    STALE_AUTO = "stale_auto"


@dataclass(frozen=True, slots=True)
class Issue:
    level: IssueLevel
    code: IssueCode
    message: str
    fixture_ids: tuple[str, ...] = ()
    output_id: str | None = None
    port_id: str | None = None
    universe: int | None = None
    channels: tuple[int, int] | None = None
