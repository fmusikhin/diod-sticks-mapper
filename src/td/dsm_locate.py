"""Чистая логика Locate (locate-test): разбор цели, порядок обхода, строки таблицы на фикстуру, раскладка номера
и адреса, сырой адрес, лист приёмки, эталон цвета ячейки для стенда.

Без TD-API и без состояния между вызовами: функции от патча ядра (``dsm_core``), его проверок и сегментов.
Индекс фикстуры ``fx`` = индекс в ``patch.fixtures`` (как в таблице ``geo_cells`` движка).
"""
from __future__ import annotations

from dataclasses import dataclass

from dsm_core.addressing import Segment, segments_by_fixture
from dsm_core.cells import cell_order
from dsm_core.model import Acceptance, Patch
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
