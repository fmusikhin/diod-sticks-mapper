"""Чистая логика Locate (locate-test): разбор цели, порядок обхода, строки таблицы на фикстуру, раскладка номера
и адреса, сырой адрес, лист приёмки, эталон цвета ячейки для стенда.

Без TD-API и без состояния между вызовами: функции от патча ядра (``dsm_core``), его проверок и сегментов.
Индекс фикстуры ``fx`` = индекс в ``patch.fixtures`` (как в таблице ``geo_cells`` движка).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from dsm_core.addressing import Segment, segments_by_fixture
from dsm_core.cells import cell_order
from dsm_core.csv_io import CsvDialect, rows_to_csv
from dsm_core.model import Acceptance, Patch, TargetKind, effective_target_ip
from dsm_core.protocol import DMX_CHANNELS, Protocol, sacn_multicast, universe_in_range, universe_range
from dsm_core.validate_types import Issue, IssueLevel

# колонки таблицы tables/locate_fx — строка на фикстуру патча (design: «tables/locate_fx»)
FX_COLUMNS = ["fx", "target", "number", "uni", "ch", "ncells", "offset"]

MODES = ("blink", "direction", "number", "address")
OTHERS = ("solo", "dim", "over")
PATTERNS = ("off", "fill", "rgbw", "chase")

# формы, которые выводит движок (как dsm_geometry.SUPPORTED_SHAPES — без импорта соседнего модуля слоя TD)
SUPPORTED_SHAPES = ("line", "polyline", "rect")

_PREFIXES = ("#", "name:", "group:", "output:", "port:")


@dataclass(frozen=True)
class Target:
    spec: str                                   # как задано: "fx7", "#12", "name:Стик 3", "group:g1", ... через запятую
    fixtures: tuple[int, ...]                   # индексы выводимых фикстур цели, по порядку патча
    skipped: tuple[tuple[int, str], ...] = ()   # (индекс, причина) — в цели, но не выводятся


class TargetError(ValueError):
    """Цель не разобрана: ``spec`` — как задано, ``reason`` — причина по-русски."""

    def __init__(self, spec: str, reason: str):
        super().__init__(f"{reason} (цель: {spec!r})")
        self.spec = spec
        self.reason = reason


def not_output_reasons(patch: Patch, issues: list[Issue],
                       segments: dict[str, list[Segment]] | None = None) -> dict[int, str]:
    """Индекс → причина для фикстур, которые движок не выводит.

    Правило то же, что в ``dsm_geometry.build_tables``: ошибка ядра (уровень error), нет профиля / выхода / сегментов
    адреса, неподдержанная форма.
    """
    if segments is None:
        segments = segments_by_fixture(patch)
    errors: dict[str, str] = {}
    for iss in issues:
        if iss.level == IssueLevel.ERROR:
            for fid in iss.fixture_ids:
                errors.setdefault(fid, f"ошибка ядра ({iss.code.value}): {iss.message}")
    types = {t.id for t in patch.fixture_types}
    outputs = {o.id for o in patch.outputs}
    res: dict[int, str] = {}
    for k, fx in enumerate(patch.fixtures):
        if fx.id in errors:
            res[k] = errors[fx.id]
        elif fx.type_id not in types or (fx.address.output_id or "") not in outputs or not segments.get(fx.id):
            res[k] = "нет адреса"
        elif fx.shape.kind not in SUPPORTED_SHAPES:
            res[k] = f"форма {fx.shape.kind!r} не поддерживается"
    return res


def _split(patch: Patch, spec: str) -> list[str]:
    """Элементы через запятую; запятая внутри ``name:`` остаётся частью имени, если следующий кусок
    не начинается с вида цели и не является id фикстуры."""
    ids = {f.id for f in patch.fixtures}
    items: list[str] = []
    for piece in spec.split(","):
        s = piece.strip()
        if (items and items[-1].startswith("name:") and s and not s.startswith(_PREFIXES) and s not in ids):
            items[-1] = f"{items[-1]},{piece}".rstrip()
            continue
        items.append(s)
    return [s for s in items if s]


def _element(patch: Patch, spec: str, item: str) -> list[int]:
    fxs = patch.fixtures
    if item.startswith("#"):
        num = item[1:].strip()
        if not num.isdigit() or not 1 <= int(num) <= len(fxs):
            raise TargetError(spec, f"нет фикстуры с номером: {item} (в патче {len(fxs)})")
        return [int(num) - 1]
    kind, sep, value = item.partition(":")
    if not sep:
        found = [k for k, f in enumerate(fxs) if f.id == item]
        if not found:
            raise TargetError(spec, f"нет такой фикстуры: {item}")
        return found
    value = value.strip()
    if kind == "name":
        found = [k for k, f in enumerate(fxs) if f.name == value]
        if not found:
            raise TargetError(spec, f"нет фикстуры с таким именем: {value}")
        return found
    if kind == "group":
        group = next((g for g in patch.groups if g.id == value), None)
        if group is None:
            raise TargetError(spec, f"нет такой группы: {value}")
        members = set(group.fixture_ids)
        found = [k for k, f in enumerate(fxs) if f.id in members]
    elif kind == "output":
        if not any(o.id == value for o in patch.outputs):
            raise TargetError(spec, f"нет такого выхода: {value}")
        found = [k for k, f in enumerate(fxs) if f.address.output_id == value]
    elif kind == "port":
        out_id, slash, port_id = value.partition("/")
        if not slash:
            raise TargetError(spec, f"порт задаётся как port:выход/порт: {item}")
        out = next((o for o in patch.outputs if o.id == out_id.strip()), None)
        if out is None:
            raise TargetError(spec, f"нет такого выхода: {out_id.strip()}")
        port = next((p for p in out.ports if p.id == port_id.strip()), None)
        if port is None:
            raise TargetError(spec, f"нет такого порта: {out.id}/{port_id.strip()}")
        pos = {f.id: k for k, f in enumerate(fxs)}
        found = [pos[i] for i in port.fixture_ids if i in pos]          # порядок цепочки порта
    else:
        raise TargetError(spec, f"неизвестный вид цели: {kind}: (fx…, #N, name:, group:, output:, port:)")
    if not found:
        raise TargetError(spec, f"нет фикстур в цели: {item}")
    return found


def resolve_target(patch: Patch, issues: list[Issue], spec: str) -> Target:
    """Строка цели → выводимые фикстуры (без повторов, по порядку патча) и пропущенные с причиной.

    Неизвестный элемент или пустая цель → ``TargetError``; цель только из не выводимых фикстур — не ошибка
    (пустой ``fixtures``, заполненный ``skipped``).
    """
    items = _split(patch, spec)
    if not items:
        raise TargetError(spec, "пустая цель")
    chosen: set[int] = set()
    for item in items:
        chosen.update(_element(patch, spec, item))
    bad = not_output_reasons(patch, issues)
    order = sorted(chosen)
    return Target(spec=spec, fixtures=tuple(k for k in order if k not in bad),
                  skipped=tuple((k, bad[k]) for k in order if k in bad))


def walk_order(patch: Patch, issues: list[Issue], only_unchecked: bool) -> list[int]:
    """Индексы выводимых фикстур по порядку патча; при ``only_unchecked`` — только со статусом приёмки ``none``."""
    bad = not_output_reasons(patch, issues)
    return [k for k, f in enumerate(patch.fixtures)
            if k not in bad and (not only_unchecked or f.acceptance == Acceptance.NONE)]


@dataclass(frozen=True)
class FxRow:
    """Строка ``tables/locate_fx``, одна на фикстуру патча (колонки ``FX_COLUMNS``)."""
    fx: int          # индекс в patch.fixtures
    target: int      # 1 — фикстура в цели
    number: int      # порядковый номер в патче, с 1
    uni: int         # юниверс начала (первый сегмент), 0 у не выводимых
    ch: int          # первый канал начала, 0 у не выводимых
    ncells: int      # ячеек в геометрии движка (по порядку данных), 0 у не выводимых
    offset: int      # сумма ncells предыдущих фикстур

    def as_row(self) -> list[str]:
        return [str(getattr(self, c)) for c in FX_COLUMNS]


def fixture_rows(patch: Patch, segments: dict[str, list[Segment]], target: Target | None,
                 issues: list[Issue] | tuple = ()) -> list[FxRow]:
    """Строка на каждую фикстуру патча, в порядке ``patch.fixtures``.

    ``ncells`` = ``len(cell_order(ft, fx))`` — столько точек движок кладёт в ``geo_cells`` на фикстуру; не выводимые
    (правило ``not_output_reasons`` по ``issues``) — с нулями адреса и ячеек.
    """
    bad = not_output_reasons(patch, list(issues), segments)
    chosen = set(target.fixtures) if target is not None else set()
    types = {t.id: t for t in patch.fixture_types}
    rows: list[FxRow] = []
    offset = 0
    for k, fx in enumerate(patch.fixtures):
        if k in bad:
            uni = ch = n = 0
        else:
            first = segments[fx.id][0]
            uni, ch, n = first.universe, first.first_channel, len(cell_order(types[fx.type_id], fx))
        rows.append(FxRow(fx=k, target=1 if k in chosen else 0, number=k + 1, uni=uni, ch=ch, ncells=n, offset=offset))
        offset += n
    return rows


_DIGIT_COLORS = ("blue", "green", "red")      # единицы, десятки, сотни; тысячи и выше — white


def _digit_groups(value: int) -> list[list[str]]:
    """Группы ячеек разрядов со старшего: цифра d — d ячеек цвета разряда, 0 — одна ``dim``."""
    if value < 0:
        raise ValueError(f"отрицательное число не показывается: {value}")
    digits = str(value)
    groups = []
    for k, d in enumerate(digits):
        pos = len(digits) - 1 - k
        color = _DIGIT_COLORS[pos] if pos < len(_DIGIT_COLORS) else "white"
        groups.append(["dim"] if d == "0" else [color] * int(d))
    return groups


def _place(parts: list[tuple[int, list[str]]], ncells: int) -> tuple[list[str], bool]:
    """Группы с числом погашенных ячеек перед каждой; целые группы, пока помещаются, хвост — ``off``."""
    cells: list[str] = []
    fits = True
    for gap, group in parts:
        piece = ["off"] * (gap if cells else 0) + group
        if len(cells) + len(piece) > ncells:
            fits = False
            break
        cells += piece
    return cells + ["off"] * (ncells - len(cells)), fits


def number_layout(number: int, ncells: int) -> tuple[list[str], bool]:
    """Цвета ячеек от начала фикстуры (по порядку данных) для числа и признак «помещается».

    Разряды со старшего, между ними одна ``off``; не помещается — столько целых старших разрядов, сколько влезает.
    """
    return _place([(1, g) for g in _digit_groups(number)], ncells)


def address_layout(universe: int, channel: int, ncells: int) -> tuple[list[str], bool]:
    """Юниверс тем же способом, две ``off``, затем канал; правила помещения как у ``number_layout``."""
    uni = [(1, g) for g in _digit_groups(universe)]
    ch = [(2 if k == 0 else 1, g) for k, g in enumerate(_digit_groups(channel))]
    return _place(uni + ch, ncells)


# ---------- сырой адрес (tables/locate_raw → output/raw) ----------

RAW_PTS_COLUMNS = ["P(0)", "P(1)", "P(2)", "Color(0)", "Color(1)", "Color(2)", "Color(3)"]
RAW_PRIM_COLUMNS = ["vertices", "DMXFixtureUniverse", "DMXFixtureChannel", "DMXFixtureNetAddress(0)",
                    "DMXFixtureNetAddress(1)", "DMXFixtureNetAddress(2)", "DMXFixtureNetAddress(3)"]
BROADCAST_IP = "255.255.255.255"      # как dsm_geometry.BROADCAST_IP


@dataclass(frozen=True)
class RawRows:
    """Строки ``tables/locate_raw``: 512 точек (канал k — точка k−1) и один line strip через них с адресом выхода."""
    pts_rows: list[list[str]]
    prims_rows: list[list[str]]
    warning: str = ""                 # юниверс не занят фикстурами этого выхода — шлётся всё равно (7.3)


class RawError(ValueError):
    """Сырой адрес отклонён: ``reason`` — причина по-русски (7.4)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def raw_target_ip(out, universe: int) -> str:
    """Цель юниверса выхода — то же правило, что у движка (``dsm_geometry._target_ip`` по ``routing_table`` ядра):
    broadcast без IP — 255.255.255.255; sACN multicast без IP — группа 239.255.hi.lo; иначе IP выхода или 127.0.0.1."""
    if out.target_kind == TargetKind.BROADCAST and not out.ip:
        return BROADCAST_IP
    if out.protocol == Protocol.SACN and out.target_kind == TargetKind.MULTICAST and not out.ip:
        return sacn_multicast(universe)
    return effective_target_ip(out)


def raw_rows(patch: Patch, output_id: str, universe: int, first: int, last: int, value: int) -> RawRows:
    """Строки сырой фикстуры: ``value`` на каналах ``first..last`` юниверса ``universe`` выхода ``output_id``,
    0 на остальных. Адрес — только цель выхода патча (7.5). Ошибка → ``RawError``, ничего не возвращается."""
    out = next((o for o in patch.outputs if o.id == output_id), None)
    if out is None:
        raise RawError(f"нет такого выхода: {output_id!r}")
    if not _is_int(universe) or universe < 0:
        raise RawError(f"юниверс — целое ≥ 0: {universe!r}")
    if not universe_in_range(out.protocol, universe):
        lo, hi = universe_range(out.protocol)
        raise RawError(f"юниверс {universe} вне диапазона {out.protocol.value} {lo}–{hi}")
    if not (_is_int(first) and _is_int(last)) or not 1 <= first <= last <= DMX_CHANNELS:
        raise RawError(f"каналы — целые 1 ≤ первый ≤ последний ≤ {DMX_CHANNELS}: {first!r}–{last!r}")
    if not _is_int(value) or not 0 <= value <= 255:
        raise RawError(f"значение — целое 0–255: {value!r}")

    on = repr(value / 255.0)              # без округления: value/255 → байт value точно
    pts: list[list[str]] = [RAW_PTS_COLUMNS[:]]
    for k in range(DMX_CHANNELS):
        pts.append([str(k), "0", "0", on if first <= k + 1 <= last else "0", "0", "0", "0"])
    octets = raw_target_ip(out, universe).split(".")
    prims = [RAW_PRIM_COLUMNS[:], [" ".join(str(k) for k in range(DMX_CHANNELS)), str(universe), "1", *octets]]

    segs = segments_by_fixture(patch)
    used = any(sg.universe == universe for fx in patch.fixtures if fx.address.output_id == out.id
               for sg in segs.get(fx.id, []))
    warning = "" if used else f"юниверс {universe} не занят патчем на выходе {out.id}: шлётся по цели выхода"
    return RawRows(pts_rows=pts, prims_rows=prims, warning=warning)


# ---------- лист приёмки (tables/locate_sheet, CSV) ----------

SHEET_COLUMNS = ["n", "id", "name", "output", "universe", "channel", "status"]
SHEET_DIALECT = CsvDialect(";", ".")      # «;» без замены точки: имена вида «1.5» не портятся


def sheet_rows(patch: Patch, segments: dict[str, list[Segment]]) -> list[list[str]]:
    """Заголовок + строка на фикстуру в порядке патча; юниверс и канал — первого сегмента (пусто без адреса)."""
    rows = [SHEET_COLUMNS[:]]
    for k, fx in enumerate(patch.fixtures):
        segs = segments.get(fx.id) or []
        uni, ch = (str(segs[0].universe), str(segs[0].first_channel)) if segs else ("", "")
        rows.append([str(k + 1), fx.id, fx.name, fx.address.output_id or "", uni, ch, fx.acceptance.value])
    return rows


def sheet_summary(patch: Patch) -> dict[str, int]:
    """Число фикстур по каждому статусу приёмки (все статусы, с нулями) и ``unchecked`` — со статусом ``none``."""
    res = {a.value: 0 for a in Acceptance}
    for fx in patch.fixtures:
        res[fx.acceptance.value] += 1
    res["unchecked"] = res[Acceptance.NONE.value]
    return res


def sheet_csv(rows: list[list[str]]) -> str:
    """CSV листа приёмки: разделитель ``;``, строка для записи в UTF-8 — писатель ядра ``rows_to_csv``."""
    return rows_to_csv(rows, SHEET_DIALECT)


# ---------- состояние Locate и эталон цвета ячейки (правила те же, что у шейдера hook/locate) ----------

RGB = tuple[float, float, float]

_LAYOUT_RGB: dict[str, RGB] = {"red": (1.0, 0.0, 0.0), "green": (0.0, 1.0, 0.0), "blue": (0.0, 0.0, 1.0),
                               "white": (1.0, 1.0, 1.0), "dim": (0.1, 0.1, 0.1), "off": (0.0, 0.0, 0.0)}
_RGBW: tuple[RGB, ...] = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 1.0, 1.0))
_WHITE: RGB = (1.0, 1.0, 1.0)
_BLACK: RGB = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class LocateState:
    """Состояние Locate; умолчания — таблица пар страницы ``Locate`` (design)."""
    target: Target | None = None
    mode: str = "blink"                   # MODES
    others: str = "solo"                  # OTHERS
    level: float = 0.3
    dim: float = 0.2
    blink_hz: float = 2.0
    smooth: bool = False
    run_sec: float = 2.0
    pattern: str = "off"                  # PATTERNS
    test_color: RGB = (1.0, 1.0, 1.0)
    walk: bool = False
    walk_loop: bool = True
    walk_unchecked: bool = False
    walk_step_s: float = 3.0
    raw: RawRows | None = None
    message: str = ""

    def active(self) -> bool:
        """Locate что-то подсвечивает: тест-паттерн не ``off`` или в цели есть выводимые фикстуры.
        Цель только из не выводимых (1.6) — Locate не меняет вывод (иначе соло гасит весь риг), причина — в сообщении."""
        return self.pattern != "off" or (self.target is not None and bool(self.target.fixtures))


def _frac(x: float) -> float:
    return x - math.floor(x)


def _mul(c, k: float) -> RGB:
    return (float(c[0]) * k, float(c[1]) * k, float(c[2]) * k)


def fixture_totals(rows: list[FxRow]) -> tuple[int, int]:
    """(сумма ``ncells`` выводимых фикстур, число выводимых фикстур) — входы бегущей точки ``chase``."""
    out = [r for r in rows if r.ncells > 0]
    return sum(r.ncells for r in out), len(out)


def _pattern_color(state: LocateState, row: FxRow, cell: int, t: float, total: int, nfx: int) -> RGB:
    if state.pattern == "fill":
        return _mul(state.test_color, state.level)
    if state.pattern == "rgbw":
        return _mul(_RGBW[int(math.floor(t / 1.0)) % 4], state.level)
    if state.pattern == "chase":
        if total <= 0 or nfx <= 0:
            return _BLACK
        p = int(math.floor(_frac(t / (state.run_sec * nfx)) * total))
        return _mul(_WHITE, state.level) if row.offset + cell == p else _BLACK
    raise ValueError(f"неизвестный тест-паттерн: {state.pattern!r}")


def _target_color(state: LocateState, row: FxRow, cell: int, t: float) -> RGB:
    n = row.ncells
    if state.mode == "blink":
        if state.smooth:
            return _mul(_WHITE, state.level * (0.5 - 0.5 * math.cos(2.0 * math.pi * _frac(t * state.blink_hz))))
        return _mul(_WHITE, state.level) if _frac(t * state.blink_hz) < 0.5 else _BLACK
    if state.mode == "direction":
        if cell == 0:
            return _mul(_LAYOUT_RGB["red"], state.level)
        if cell == n - 1:
            return _mul(_LAYOUT_RGB["blue"], state.level)
        if n >= 3 and cell == 1 + int(math.floor(_frac(t / state.run_sec) * (n - 2))):
            return _mul(_WHITE, state.level)
        return _BLACK
    if state.mode == "number":
        return _mul(_LAYOUT_RGB[number_layout(row.number, n)[0][cell]], state.level)
    if state.mode == "address":
        return _mul(_LAYOUT_RGB[address_layout(row.uni, row.ch, n)[0][cell]], state.level)
    raise ValueError(f"неизвестный режим Locate: {state.mode!r}")


def expected_color(state: LocateState, row: FxRow, cell: int, active: bool, content, t: float,
                   total: int, nfx: int) -> RGB:
    """Эталон цвета одной ячейки (float RGB 0..1) — то, что шейдер ``hook/locate`` кладёт в ``Color``.

    ``active`` — ячейка не отключена (``disabled``/``every_n``); ``content`` — цвет контента ячейки; ``t`` — время, с;
    ``total``/``nfx`` — ``fixture_totals`` выводимых фикстур (для ``chase``). Цвет цели и паттерна не зависит от
    ``content`` и ``Master`` (9.2).
    """
    if not active or not state.active():
        return content
    if state.pattern != "off":
        return _pattern_color(state, row, cell, t, total, nfx) if row.ncells > 0 else content
    if row.target == 1:
        return _target_color(state, row, cell, t)
    if state.others == "solo":
        return _BLACK
    if state.others == "dim":
        return _mul(content, state.dim)
    if state.others == "over":
        return content
    raise ValueError(f"неизвестное поведение остальных: {state.others!r}")


def step(order: list[int], current: int | None, delta: int, loop: bool) -> int | None:
    """Следующая (``delta=1``) или предыдущая (``-1``) фикстура обхода.

    Без цели — первая в ``order``. Цель вне ``order`` (не выводится, отфильтрована) — ближайшая по порядку патча
    в сторону шага. На краю: ``loop`` — по кругу, иначе остаётся на крайней. Пустой ``order`` → ``None``.
    """
    if not order:
        return None
    if current is None:
        return order[0]
    if current in order:
        k = order.index(current) + delta
        if 0 <= k < len(order):
            return order[k]
        return order[k % len(order)] if loop else order[0 if k < 0 else -1]
    if delta > 0:
        nxt = [k for k in order if k > current]
        return nxt[0] if nxt else (order[0] if loop else order[-1])
    prv = [k for k in order if k < current]
    return prv[-1] if prv else (order[-1] if loop else order[0])
