"""Модель патча: неизменяемые dataclasses и перечисления.

Модель ничего не считает, кроме производных свойств профиля. Инварианты полей проверяет
``schema`` при загрузке и ``ops`` при правке; конструкторы доверяют вызывающему.
У объектов с id есть ``extra`` — неизвестные поля документа, которые сохраняются при записи.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Union

from .protocol import Protocol

Point = tuple[float, float]

COLOR_LETTERS = "RGBWA"


class TargetKind(str, Enum):
    UNICAST = "unicast"
    BROADCAST = "broadcast"
    MULTICAST = "multicast"


class FixtureKind(str, Enum):
    STRIP = "strip"
    STICK = "stick"
    MATRIX = "matrix"
    BAR = "bar"
    CUSTOM = "custom"


class AddressMode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    UNASSIGNED = "unassigned"


class Direction(str, Enum):
    FORWARD = "forward"
    REVERSE = "reverse"


class Acceptance(str, Enum):
    NONE = "none"
    OK = "ok"
    REVERSED = "reversed"
    DEAD = "dead"
    MISPLACED = "misplaced"
    WRONG_ADDRESS = "wrong_address"


class MatrixLayout(str, Enum):
    """16 раскладок: угол старта (tl/tr/bl/br) × первая линия (rows/cols) × змейка."""

    TL_ROWS = "tl_rows"
    TL_ROWS_SNAKE = "tl_rows_snake"
    TL_COLS = "tl_cols"
    TL_COLS_SNAKE = "tl_cols_snake"
    TR_ROWS = "tr_rows"
    TR_ROWS_SNAKE = "tr_rows_snake"
    TR_COLS = "tr_cols"
    TR_COLS_SNAKE = "tr_cols_snake"
    BL_ROWS = "bl_rows"
    BL_ROWS_SNAKE = "bl_rows_snake"
    BL_COLS = "bl_cols"
    BL_COLS_SNAKE = "bl_cols_snake"
    BR_ROWS = "br_rows"
    BR_ROWS_SNAKE = "br_rows_snake"
    BR_COLS = "br_cols"
    BR_COLS_SNAKE = "br_cols_snake"


# ---------- профиль ----------

@dataclass(frozen=True, slots=True)
class Channel:
    name: str
    role: str = "control"      # "color" | "control"
    default: int = 0           # 0–255 (для 16 бит — старший байт)
    bits: int = 8              # 8 | 16

    @property
    def width(self) -> int:
        return 2 if self.bits == 16 else 1


@dataclass(frozen=True, slots=True)
class ChannelBlock:
    channels: tuple[Channel, ...] = ()

    @property
    def width(self) -> int:
        return sum(c.width for c in self.channels)


@dataclass(frozen=True, slots=True)
class FixtureType:
    id: str
    name: str
    kind: FixtureKind
    cells: int                                   # число ячеек (matrix: width*height)
    width: int = 1
    height: int = 1
    layout: MatrixLayout = MatrixLayout.TL_ROWS
    custom_cells: tuple[Point, ...] = ()         # kind custom: относительные позиции
    color_order: str = "RGB"
    bits: int = 8
    head: ChannelBlock = ChannelBlock()          # общие каналы прибора
    cell_extra: ChannelBlock = ChannelBlock()    # нецветовые каналы ячейки, после цветовых
    tail: ChannelBlock = ChannelBlock()
    pitch_mm: float | None = None
    cells_per_m: float | None = None
    length_mm: float | None = None
    gamma: float = 1.0
    white_balance: tuple[float, float, float] = (1.0, 1.0, 1.0)
    white_mode: str = "none"
    watts_per_cell: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def channels_per_cell(self) -> int:
        return len(self.color_order) * (2 if self.bits == 16 else 1) + self.cell_extra.width

    @property
    def total_channels(self) -> int:
        return self.head.width + self.cells * self.channels_per_cell + self.tail.width


# ---------- формы ----------

@dataclass(frozen=True, slots=True)
class LineShape:
    p1: Point
    p2: Point
    kind: Literal["line"] = "line"


@dataclass(frozen=True, slots=True)
class PolylineShape:
    points: tuple[Point, ...]
    kind: Literal["polyline"] = "polyline"


@dataclass(frozen=True, slots=True)
class RectShape:
    center: Point
    size: Point
    rotation_deg: float = 0.0
    kind: Literal["rect"] = "rect"


@dataclass(frozen=True, slots=True)
class CurveShape:
    points: tuple[Point, ...]
    kind: Literal["curve"] = "curve"


@dataclass(frozen=True, slots=True)
class ArcShape:
    center: Point
    radius: float
    start_deg: float
    end_deg: float
    kind: Literal["arc"] = "arc"


@dataclass(frozen=True, slots=True)
class CustomShape:
    points: tuple[Point, ...]
    kind: Literal["custom"] = "custom"


Shape = Union[LineShape, PolylineShape, RectShape, CurveShape, ArcShape, CustomShape]

SHAPE_KINDS: tuple[str, ...] = ("line", "polyline", "rect", "curve", "arc", "custom")


@dataclass(frozen=True, slots=True)
class Sampling:
    kind: str = "point"          # point | area | elongated
    size: float = 0.0
    width: float = 0.0
    along: bool = True


# ---------- фикстура ----------

@dataclass(frozen=True, slots=True)
class Address:
    mode: AddressMode = AddressMode.UNASSIGNED
    output_id: str | None = None
    port_id: str | None = None
    universe: int | None = None      # port-address (Art-Net) или 1–63999 (sACN)
    channel: int | None = None       # 1–512, первый канал фикстуры
    channel_offset: int = 0          # пропуск каналов перед фикстурой при автоадресации


@dataclass(frozen=True, slots=True)
class Fixture:
    id: str
    name: str
    type_id: str
    shape: Shape
    groups: tuple[str, ...] = ()
    sampling: Sampling = Sampling()
    output_shape: Shape | None = None
    direction: Direction = Direction.FORWARD
    cell_offset: int = 0
    disabled: frozenset[int] = frozenset()
    every_n: int = 1
    length_mm: float | None = None
    address: Address = Address()
    brightness: float = 1.0
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    mute: bool = False
    acceptance: Acceptance = Acceptance.NONE
    extra: dict = field(default_factory=dict)


# ---------- выход ----------

@dataclass(frozen=True, slots=True)
class OutputRules:
    whole_pixel: bool = True
    auto_span: bool = True
    skip_511_512: bool = False


@dataclass(frozen=True, slots=True)
class Port:
    id: str
    number: int
    fixture_ids: tuple[str, ...] = ()
    new_universe: bool = True
    max_cells: int | None = None
    max_universes: int | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Output:
    id: str
    name: str
    protocol: Protocol
    start_universe: int
    target_kind: TargetKind = TargetKind.UNICAST
    ip: str | None = None            # None — цель не задана → 127.0.0.1
    universe_count: int | None = None
    rules: OutputRules = OutputRules()
    ports: tuple[Port, ...] = ()
    fixture_ids: tuple[str, ...] = ()   # фикстуры выхода вне портов, по порядку
    rate_hz: float = 60.0
    art_sync: bool = False
    priority: int = 100
    delay_ms: float = 0.0
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Group:
    id: str
    name: str
    fixture_ids: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Canvas:
    width: int = 1280
    height: int = 720
    units: str = "px"            # px | m
    px_per_m: float | None = None
    background: str | None = None   # относительный путь


@dataclass(frozen=True, slots=True)
class Patch:
    schema_version: int
    canvas: Canvas = Canvas()
    fixture_types: tuple[FixtureType, ...] = ()
    fixtures: tuple[Fixture, ...] = ()
    outputs: tuple[Output, ...] = ()
    groups: tuple[Group, ...] = ()
    extra: dict = field(default_factory=dict)


# ---------- поиск по id (чистые функции, модель остаётся frozen+slots) ----------

def get_fixture_type(patch: Patch, ft_id: str) -> FixtureType:
    for ft in patch.fixture_types:
        if ft.id == ft_id:
            return ft
    raise KeyError(f"профиль не найден: {ft_id}")


def get_fixture(patch: Patch, fx_id: str) -> Fixture:
    for fx in patch.fixtures:
        if fx.id == fx_id:
            return fx
    raise KeyError(f"фикстура не найдена: {fx_id}")


def get_output(patch: Patch, out_id: str) -> Output:
    for o in patch.outputs:
        if o.id == out_id:
            return o
    raise KeyError(f"выход не найден: {out_id}")


def get_group(patch: Patch, grp_id: str) -> Group:
    for g in patch.groups:
        if g.id == grp_id:
            return g
    raise KeyError(f"группа не найдена: {grp_id}")


def get_port(output: Output, port_id: str) -> Port:
    for p in output.ports:
        if p.id == port_id:
            return p
    raise KeyError(f"порт не найден: {port_id}")


def effective_target_ip(output: Output) -> str:
    """Цель для маршрутизации: IP выхода, multicast-группа sACN или 127.0.0.1, пока цель не задана."""
    if output.ip:
        return output.ip
    return "127.0.0.1"
