"""Чистая логика карты DMX (patch-view): раскладка строк юниверсов, прямоугольники карты и крупного юниверса,
подписи, цвет фикстуры, сводка выхода.

Модуль не импортирует TouchDesigner, ``dsm_geometry`` и ``dsm_locate``; единственная зависимость — ядро ``dsm_core``.
Координаты — в долях области карты (0–1): по x канал ``k`` занимает ``[(k-1)/512, k/512)``, по y строка ``i``
(сверху) занимает ``[1-(i+1)/rows, 1-i/rows)``. Пиксели (минимум 16 px на строку, 20 px на клетку) — дело панели.
"""
from __future__ import annotations

import colorsys
import zlib
from dataclasses import dataclass, field

from dsm_core.addressing import Segment
from dsm_core.model import Patch, get_fixture, get_fixture_type
from dsm_core.protocol import ARTNET_MAX, Protocol, dotted, format_universe
from dsm_core.validate_types import Issue, IssueLevel

Rows = list[list[str]]
RGB = tuple[float, float, float]

DMX_CHANNELS = 512
RECT_COLUMNS = ["x", "y", "w", "h", "r", "g", "b", "a", "layer", "kind", "id"]
LABEL_COLUMNS = ["x", "y", "text", "kind", "id", "wide"]
LEVEL_RANK = {"": 0, IssueLevel.WARNING.value: 1, IssueLevel.ERROR.value: 2}


@dataclass(frozen=True)
class Geom:
    """Параметры раскладки и цвета подложек/обводок. Всё в долях области; пиксели — у панели."""

    bg_declared: RGB = (0.16, 0.16, 0.18)        # юниверс в заявленном диапазоне выхода
    bg_undeclared: RGB = (0.30, 0.20, 0.12)      # занят, но вне заявленного диапазона
    skip_color: RGB = (0.08, 0.08, 0.08)         # недоступные каналы 511–512
    error_color: RGB = (1.0, 0.15, 0.15)
    warning_color: RGB = (1.0, 0.85, 0.1)
    select_color: RGB = (1.0, 1.0, 1.0)
    head_tail_alpha: float = 0.5                 # head/tail — тот же цвет полупрозрачно (штриховка — по kind в рендере)
    label_min_frac: float = 40 / 1280            # сегмент «достаточно широк» для подписи (2.3)
    zoom_cols: int = 32                          # сетка крупного юниверса 32×16 (2.4)
    zoom_rows: int = 16


@dataclass(frozen=True)
class RowInfo:
    output_id: str
    universe: int
    dotted: str              # Art-Net: net.subnet.universe; sACN: сквозной номер
    index: int               # порядок строки сверху
    declared: bool           # в заявленном диапазоне выхода
    used_channels: int       # занятых каналов (наложения считаются один раз)


@dataclass(frozen=True)
class SegInfo:
    fixture_id: str
    row: int                 # индекс строки в MapLayout.rows
    first_channel: int
    last_channel: int
    kind: str                # head | cells | tail
    cells: int
    color: RGB
    issue_level: str         # "error" | "warning" | ""


@dataclass(frozen=True)
class MapLayout:
    rows: tuple[RowInfo, ...]
    segments: tuple[SegInfo, ...]
    skip_511_512: dict[str, bool]                      # по выходу
    names: dict[str, str] = field(default_factory=dict)   # fixture_id → подпись (имя или id)
    protocols: dict[str, Protocol] = field(default_factory=dict)   # output_id → протокол (подписи юниверсов)


@dataclass(frozen=True)
class Hit:
    """Попадание в карту (4.1): всё ``None`` — точка вне области; ``segment``/``fixture_id`` ``None`` — подложка."""

    row: int | None
    channel: int | None          # 1–512
    segment: SegInfo | None
    fixture_id: str | None


NO_HIT = Hit(None, None, None, None)


# ---------- цвет фикстуры (2.6) ----------

def fixture_color(fixture_id: str) -> RGB:
    """Стабильный цвет по ``id`` (crc32, не рандомизированный ``hash``): тон по хэшу, насыщенность и яркость —
    по двум следующим битовым полям, чтобы близкие тона ещё и различались."""
    h = zlib.crc32(fixture_id.encode("utf-8"))
    hue = ((h & 0xFFFF) * 0.6180339887) % 1.0           # золотое сечение раздвигает близкие хэши
    sat = 0.55 + 0.4 * ((h >> 16) & 0x7) / 7.0
    val = 0.7 + 0.3 * ((h >> 19) & 0x7) / 7.0
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return (round(r, 6), round(g, 6), round(b, 6))


# ---------- раскладка (2.1, 2.2, 2.6, 3.1) ----------

def _issue_level(fixture_id: str, output_id: str, seg: Segment, issues: list[Issue]) -> str:
    """Максимальный уровень проблем, задевающих фикстуру: названа в ``fixture_ids`` — или проблема без фикстур
    про её юниверс выхода (``universe``; при ``channels`` — только пересечение каналов)."""
    best = ""
    for iss in issues:
        if iss.fixture_ids:
            hit = fixture_id in iss.fixture_ids
        else:
            hit = iss.output_id == output_id and iss.universe == seg.universe and (
                iss.channels is None or (iss.channels[0] <= seg.last_channel and seg.first_channel <= iss.channels[1]))
        if hit and LEVEL_RANK[iss.level.value] > LEVEL_RANK[best]:
            best = iss.level.value
    return best


def _declared(out, universe: int) -> bool:
    if universe < out.start_universe:
        return False
    return out.universe_count is None or universe < out.start_universe + out.universe_count


def _union_len(spans: list[tuple[int, int]]) -> int:
    total, cur = 0, None
    for a, b in sorted(spans):
        if cur is None or a > cur[1] + 1:
            if cur is not None:
                total += cur[1] - cur[0] + 1
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    if cur is not None:
        total += cur[1] - cur[0] + 1
    return total


def map_layout(patch: Patch, segments: dict[str, list[Segment]], issues: list[Issue],
               output_id: str | None) -> MapLayout:
    """Строки карты: выходы в порядке патча (``output_id=None`` — все), юниверсы по возрастанию — занятые ∪ заявленные.
    Фикстуры без сегментов в карте нет (они — в списке «не размещены»)."""
    outputs = [o for o in patch.outputs if output_id is None or o.id == output_id]
    fixtures = [fx for fx in patch.fixtures if segments.get(fx.id)]
    rows: list[RowInfo] = []
    segs: list[SegInfo] = []
    for out in outputs:
        own = [fx for fx in fixtures if fx.address.output_id == out.id]
        universes = {s.universe for fx in own for s in segments[fx.id]}
        if out.universe_count is not None:
            universes.update(range(out.start_universe, out.start_universe + out.universe_count))
        row_of: dict[int, int] = {}
        for u in sorted(universes):
            spans = [(s.first_channel, s.last_channel) for fx in own for s in segments[fx.id] if s.universe == u]
            row_of[u] = len(rows)
            # юниверс за пределом Art-Net (ошибка ядра universe_range) — строка остаётся, подпись сквозная
            label = dotted(u) if out.protocol == Protocol.ARTNET and 0 <= u <= ARTNET_MAX else str(u)
            rows.append(RowInfo(out.id, u, label, len(rows), _declared(out, u), _union_len(spans)))
        for fx in own:
            color = fixture_color(fx.id)
            for s in segments[fx.id]:
                segs.append(SegInfo(fx.id, row_of[s.universe], s.first_channel, s.last_channel, s.kind, s.n_cells,
                                    color, _issue_level(fx.id, out.id, s, issues)))
    return MapLayout(tuple(rows), tuple(segs), {o.id: o.rules.skip_511_512 for o in outputs},
                     {fx.id: fx.name or fx.id for fx in fixtures}, {o.id: o.protocol for o in outputs})


# ---------- прямоугольники (2.2, 2.4, 3.1, 8.2) ----------

def _f(v: float) -> str:
    return f"{v:.6f}"


def _rect(x: float, y: float, w: float, h: float, rgb: RGB, a: float, layer: int, kind: str, fid: str) -> list[str]:
    return [_f(x), _f(y), _f(w), _f(h), _f(rgb[0]), _f(rgb[1]), _f(rgb[2]), _f(a), str(layer), kind, fid]


def _level_color(level: str, geom: Geom) -> RGB | None:
    if level == IssueLevel.ERROR.value:
        return geom.error_color
    if level == IssueLevel.WARNING.value:
        return geom.warning_color
    return None


def _overlays(seg: SegInfo, boxes: list[tuple[float, float, float, float]], selected: set[str], geom: Geom,
              layer2: Rows, layer3: Rows) -> None:
    col = _level_color(seg.issue_level, geom)
    for x, y, w, h in boxes:
        if col is not None:
            layer2.append(_rect(x, y, w, h, col, 1.0, 2, "issue", seg.fixture_id))
        if seg.fixture_id in selected:
            layer3.append(_rect(x, y, w, h, geom.select_color, 1.0, 3, "select", seg.fixture_id))


def map_rects(layout: MapLayout, selected: set[str], geom: Geom) -> Rows:
    """Строки ``pv_rects``: слой 0 — подложки строк и клетки 511–512 при ``skip_511_512``; 1 — сегменты цветом
    фикстуры (head/tail полупрозрачно); 2 — обводки проблем; 3 — выделение."""
    n = len(layout.rows)
    rh = 1.0 / n if n else 1.0
    ch = 1.0 / DMX_CHANNELS
    layers: list[Rows] = [[], [], [], []]
    for r in layout.rows:
        y = 1.0 - (r.index + 1) * rh
        layers[0].append(_rect(0.0, y, 1.0, rh, geom.bg_declared if r.declared else geom.bg_undeclared, 1.0, 0, "bg", ""))
        if layout.skip_511_512.get(r.output_id):
            layers[0].append(_rect(510 * ch, y, 2 * ch, rh, geom.skip_color, 1.0, 0, "skip", ""))
    for s in layout.segments:
        y = 1.0 - (s.row + 1) * rh
        x, w = (s.first_channel - 1) * ch, (s.last_channel - s.first_channel + 1) * ch
        a = 1.0 if s.kind == "cells" else geom.head_tail_alpha
        layers[1].append(_rect(x, y, w, rh, s.color, a, 1, s.kind, s.fixture_id))
        _overlays(s, [(x, y, w, rh)], selected, geom, layers[2], layers[3])
    return [RECT_COLUMNS[:]] + layers[0] + layers[1] + layers[2] + layers[3]


def _zoom_boxes(first: int, last: int, geom: Geom) -> list[tuple[float, float, float, float]]:
    """Каналы ``first..last`` на сетке крупного юниверса — прямоугольник на каждую задетую строку сетки."""
    cols, grid_rows = geom.zoom_cols, geom.zoom_rows
    cw, chh = 1.0 / cols, 1.0 / grid_rows
    boxes = []
    k = first
    while k <= last:
        gr, gc = (k - 1) // cols, (k - 1) % cols
        end = min(last, gr * cols + cols)                       # последний канал этой строки сетки
        boxes.append((gc * cw, 1.0 - (gr + 1) * chh, (end - k + 1) * cw, chh))
        k = end + 1
    return boxes


def zoom_rects(layout: MapLayout, row: int, geom: Geom, selected: set[str] | frozenset[str] = frozenset()) -> Rows:
    """Крупный юниверс (2.4): 512 клеток сеткой ``zoom_cols × zoom_rows`` (слой 0, ``cell``; 511–512 — ``skip``
    при правиле выхода) и сегменты строки по клеткам поверх (слои 1–3)."""
    info = layout.rows[row]
    cols, grid_rows = geom.zoom_cols, geom.zoom_rows
    cw, chh = 1.0 / cols, 1.0 / grid_rows
    skip = bool(layout.skip_511_512.get(info.output_id))
    bg = geom.bg_declared if info.declared else geom.bg_undeclared
    layers: list[Rows] = [[], [], [], []]
    for k in range(1, DMX_CHANNELS + 1):
        gr, gc = (k - 1) // cols, (k - 1) % cols
        unavailable = skip and k > 510
        layers[0].append(_rect(gc * cw, 1.0 - (gr + 1) * chh, cw, chh, geom.skip_color if unavailable else bg, 1.0, 0,
                               "skip" if unavailable else "cell", ""))
    for s in layout.segments:
        if s.row != row:
            continue
        boxes = _zoom_boxes(s.first_channel, s.last_channel, geom)
        a = 1.0 if s.kind == "cells" else geom.head_tail_alpha
        for x, y, w, h in boxes:
            layers[1].append(_rect(x, y, w, h, s.color, a, 1, s.kind, s.fixture_id))
        _overlays(s, boxes, set(selected), geom, layers[2], layers[3])
    return [RECT_COLUMNS[:]] + layers[0] + layers[1] + layers[2] + layers[3]


# ---------- подписи (2.1, 2.3) ----------

def label_rows(layout: MapLayout, geom: Geom) -> Rows:
    """``pv_labels``: подпись юниверса слева каждой строки (сквозной и точечный вид) и подпись каждого сегмента
    «имя · n px» с признаком ``wide`` — сегмент не уже ``geom.label_min_frac``."""
    n = len(layout.rows)
    rh = 1.0 / n if n else 1.0
    ch = 1.0 / DMX_CHANNELS
    out: Rows = [LABEL_COLUMNS[:]]
    for r in layout.rows:
        proto = layout.protocols.get(r.output_id, Protocol.SACN)
        out.append([_f(0.0), _f(1.0 - (r.index + 0.5) * rh), format_universe(proto, r.universe), "universe", "", "1"])
    for s in layout.segments:
        w = (s.last_channel - s.first_channel + 1) * ch
        name = layout.names.get(s.fixture_id, s.fixture_id)
        out.append([_f((s.first_channel - 1) * ch), _f(1.0 - (s.row + 0.5) * rh), f"{name} · {s.cells} px", "segment",
                    s.fixture_id, "1" if w >= geom.label_min_frac else "0"])
    return out


# ---------- попадание и адрес по точке (4.1, 4.2) ----------

def _inside(u: float, v: float) -> bool:
    return 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0


def _cell_index(frac: float, count: int) -> int:
    """Индекс клетки по доле ``frac`` при ``count`` клетках: ``floor(frac·count)`` с зажимом в ``count-1`` (край 1.0)."""
    return min(int(frac * count), count - 1)


def _segment_at(layout: MapLayout, row: int, channel: int) -> SegInfo | None:
    """Сегмент строки под каналом. При наложении (конфликт ядра) побеждает последний в ``layout.segments`` —
    он же нарисован поверх в ``map_rects``/``zoom_rects`` (слои идут в порядке списка)."""
    found = None
    for s in layout.segments:
        if s.row == row and s.first_channel <= channel <= s.last_channel:
            found = s
    return found


def hit(layout: MapLayout, u: float, v: float) -> Hit:
    """Попадание в карту по долям области (``u`` слева направо, ``v`` снизу вверх, как панель TD). Та же геометрия,
    что в ``map_rects``: строка ``i`` занимает ``[1-(i+1)/rows, 1-i/rows)`` (считаем сверху: ``i = floor((1-v)·rows)``),
    канал ``k`` — ``[(k-1)/512, k/512)``; края ``u=1``/``v=0`` зажимаются в последние клетку/строку."""
    n = len(layout.rows)
    if not n or not _inside(u, v):
        return NO_HIT
    row = _cell_index(1.0 - v, n)
    channel = 1 + _cell_index(u, DMX_CHANNELS)
    seg = _segment_at(layout, row, channel)
    return Hit(row, channel, seg, seg.fixture_id if seg else None)


def zoom_hit(layout: MapLayout, row: int, u: float, v: float, geom: Geom = Geom()) -> Hit:
    """Попадание в крупный юниверс строки ``row`` (сетка ``zoom_cols × zoom_rows`` как в ``zoom_rects``: клетка
    ``k`` → колонка ``(k-1) % cols`` слева, строка ``(k-1) // cols`` сверху). ``row`` вне диапазона → ``IndexError``."""
    layout.rows[row]                     # IndexError на плохой строке — до проверки точки
    if not _inside(u, v):
        return NO_HIT
    gc = _cell_index(u, geom.zoom_cols)
    gr = _cell_index(1.0 - v, geom.zoom_rows)
    channel = min(1 + gr * geom.zoom_cols + gc, DMX_CHANNELS)
    seg = _segment_at(layout, row, channel)
    return Hit(row, channel, seg, seg.fixture_id if seg else None)


def _snap_channel(channel: int, cpc: int) -> int:
    """Ближайшее начало ячейки ``1 + k·cpc`` (сетка от канала 1, head-каналы не учитываются); при равном расстоянии —
    вверх. Результат не выходит за 512: при необходимости — последнее начало ячейки ≤ 512."""
    if cpc <= 1:
        return channel
    k = (channel - 1 + cpc // 2) // cpc
    snapped = 1 + k * cpc
    while snapped > DMX_CHANNELS:
        snapped -= cpc
    return max(1, snapped)


def drop_address(layout: MapLayout, patch: Patch, fixture_id: str, row: int, channel: int,
                 snap: bool) -> tuple[str, int, int]:
    """Адрес по точке отпускания (4.1, 4.2): выход и юниверс строки ``row`` (строка другого выхода — перенос на него),
    канал зажат в 1–512; при ``snap`` — к ближайшему началу ячейки по ``channels_per_cell`` типа фикстуры.
    ``row`` вне диапазона → ``ValueError``; неизвестная фикстура → ``KeyError`` ядра."""
    if not 0 <= row < len(layout.rows):
        raise ValueError(f"строка карты {row} вне диапазона 0–{len(layout.rows) - 1}")
    info = layout.rows[row]
    fx = get_fixture(patch, fixture_id)
    ch = max(1, min(DMX_CHANNELS, channel))
    if snap:
        ch = _snap_channel(ch, get_fixture_type(patch, fx.type_id).channels_per_cell)
    return info.output_id, info.universe, ch


def _first_segment(layout: MapLayout, fixture_id: str) -> SegInfo | None:
    """Первый сегмент фикстуры в раскладке (минимальная строка, минимальный канал) — его начало и есть адрес
    фикстуры (``Address.channel``: head-блок или первая ячейка), для auto и manual одинаково."""
    own = [s for s in layout.segments if s.fixture_id == fixture_id]
    return min(own, key=lambda s: (s.row, s.first_channel)) if own else None


def drop_address_from_grab(layout: MapLayout, patch: Patch, fixture_id: str, grab: Hit, u: float, v: float,
                           snap: bool) -> tuple[str, int, int]:
    """Адрес при перетаскивании: сдвиг курсора от точки захвата переносится на адрес фикстуры — начало её первого
    сегмента (``_first_segment``), а не захваченного: строка = ``first.row + (cur.row − grab.row)``, канал =
    ``first.first_channel + (cur.channel − grab.channel)``. Поэтому отпускание на месте всегда даёт текущий адрес
    (с привязкой — ближайшую сетку от него), а захват head/tail или продолжения в следующем юниверсе сохраняет
    разницу строк. Строка, ушедшая за край карты, зажимается в 0 / последнюю; затем ``drop_address``.
    Захват без сегмента или фикстура не в раскладке — смещения нет (адрес по точке курсора). Курсор вне области
    карты → ``ValueError``."""
    cur = hit(layout, u, v)
    if cur.row is None or cur.channel is None:
        raise ValueError("точка отпускания вне области карты")
    row, channel = cur.row, cur.channel
    first = _first_segment(layout, fixture_id)
    if first is not None and grab.segment is not None and grab.row is not None and grab.channel is not None:
        row = max(0, min(len(layout.rows) - 1, first.row + (cur.row - grab.row)))
        channel = first.first_channel + (cur.channel - grab.channel)
    return drop_address(layout, patch, fixture_id, row, channel, snap)


# ---------- сводка выхода (2.7) ----------

def output_summary(patch: Patch, segments: dict[str, list[Segment]], output_id: str) -> dict[str, int]:
    """Юниверсов занято / заявлено (``universe_count`` или занятые), каналов занято, фикстур на выходе, ячеек."""
    out = next((o for o in patch.outputs if o.id == output_id), None)
    own = [fx for fx in patch.fixtures if fx.address.output_id == output_id]
    spans: dict[int, list[tuple[int, int]]] = {}
    cells = 0
    for fx in own:
        for s in segments.get(fx.id, []):
            spans.setdefault(s.universe, []).append((s.first_channel, s.last_channel))
            cells += s.n_cells
    used = len(spans)
    declared = out.universe_count if out is not None and out.universe_count is not None else used
    return {"universes_used": used, "universes_declared": declared,
            "channels_used": sum(_union_len(v) for v in spans.values()), "fixtures": len(own), "cells": cells}
