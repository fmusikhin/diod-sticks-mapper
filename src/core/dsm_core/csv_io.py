"""CSV: экспорт фикстур и таблиц, импорт фикстур. Диалекты: ``,`` с точкой и ``;`` с запятой (русский Excel)."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, replace

from . import ops
from .errors import OperationError
from .model import (Acceptance, Address, AddressMode, ArcShape, CurveShape, CustomShape, Direction, Fixture, LineShape,
                    Patch, PolylineShape, RectShape, Shape, get_fixture_type, get_output)
from .tables import Rows


@dataclass(frozen=True, slots=True)
class CsvDialect:
    delimiter: str = ","
    decimal: str = "."


RU = CsvDialect(";", ",")

FIXTURE_COLUMNS: tuple[str, ...] = ("id", "name", "fixture_type", "groups", "output", "port", "universe", "channel",
                                    "address_mode", "channel_offset", "cells", "length_mm", "direction", "cell_offset", "shape",
                                    "x1", "y1", "x2", "y2", "points", "disabled", "every_n", "status")
REQUIRED = ("name", "fixture_type", "shape")


@dataclass(frozen=True, slots=True)
class CsvRowError:
    line: int
    column: str
    reason: str


@dataclass(frozen=True, slots=True)
class CsvImportResult:
    patch: Patch
    created: tuple[str, ...]
    updated: tuple[str, ...]
    errors: tuple[CsvRowError, ...]


def detect_dialect(text: str) -> CsvDialect:
    first = text.lstrip("﻿").split("\n", 1)[0]
    return RU if ";" in first and "," not in first else CsvDialect()


def _num(x: float, d: CsvDialect) -> str:
    s = repr(round(float(x), 3))
    if s.endswith(".0"):
        s = s[:-2]
    return s.replace(".", d.decimal)


def _pt(p, d: CsvDialect) -> str:
    return f"{_num(p[0], d)} {_num(p[1], d)}"


def _shape_cols(s: Shape, d: CsvDialect) -> tuple[str, str, str, str, str, str]:
    if isinstance(s, LineShape):
        return ("line", _num(s.p1[0], d), _num(s.p1[1], d), _num(s.p2[0], d), _num(s.p2[1], d), "")
    if isinstance(s, (PolylineShape, CurveShape, CustomShape)):
        return (s.kind, "", "", "", "", ";".join(_pt(p, d) for p in s.points))
    if isinstance(s, RectShape):
        return ("rect", "", "", "", "", f"{_pt(s.center, d)};{_pt(s.size, d)};{_num(s.rotation_deg, d)}")
    return ("arc", "", "", "", "", f"{_pt(s.center, d)};{_num(s.radius, d)};{_num(s.start_deg, d)} {_num(s.end_deg, d)}")


def rows_to_csv(rows: Rows, dialect: CsvDialect = CsvDialect()) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=dialect.delimiter, lineterminator="\n")
    for r in rows:
        w.writerow([c.replace(".", dialect.decimal) if dialect.decimal != "." and _is_num(c) else c for c in r])
    return buf.getvalue()


def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def fixtures_rows(patch: Patch, dialect: CsvDialect = CsvDialect()) -> Rows:
    rows: Rows = [list(FIXTURE_COLUMNS)]
    types = {t.id: t for t in patch.fixture_types}
    from .cells import cell_count
    for f in patch.fixtures:
        a = f.address
        ft = types.get(f.type_id)
        kind, x1, y1, x2, y2, pts = _shape_cols(f.shape, dialect)
        rows.append([f.id, f.name, f.type_id, "|".join(f.groups), a.output_id or "", a.port_id or "",
                     "" if a.universe is None else str(a.universe), "" if a.channel is None else str(a.channel),
                     a.mode.value, str(a.channel_offset), str(cell_count(ft, f)) if ft else "",
                     _num(f.length_mm, dialect) if f.length_mm else "", f.direction.value,
                     str(f.cell_offset), kind, x1, y1, x2, y2, pts, "|".join(str(i) for i in sorted(f.disabled)),
                     str(f.every_n), f.acceptance.value])
    return rows


def export_fixtures_csv(patch: Patch, dialect: CsvDialect = CsvDialect()) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=dialect.delimiter, lineterminator="\n")
    for r in fixtures_rows(patch, dialect):
        w.writerow(r)
    return buf.getvalue()


# ---------- импорт ----------

def _f(s: str, d: CsvDialect) -> float:
    return float(s.strip().replace(d.decimal, ".")) if d.decimal != "." else float(s.strip())


def _pt_parse(s: str, d: CsvDialect) -> tuple[float, float]:
    a, b = s.split()
    return (_f(a, d), _f(b, d))


def _parse_shape(row: dict, d: CsvDialect) -> Shape:
    kind = row["shape"].strip()
    pts = row.get("points", "").strip()
    if kind == "line":
        return LineShape((_f(row["x1"], d), _f(row["y1"], d)), (_f(row["x2"], d), _f(row["y2"], d)))
    if kind in ("polyline", "curve", "custom"):
        points = tuple(_pt_parse(p, d) for p in pts.split(";") if p.strip())
        cls = {"polyline": PolylineShape, "curve": CurveShape, "custom": CustomShape}[kind]
        return cls(points)
    if kind == "rect":
        c, s, r = pts.split(";")
        return RectShape(_pt_parse(c, d), _pt_parse(s, d), _f(r, d))
    if kind == "arc":
        c, r, a = pts.split(";")
        a0, a1 = a.split()
        return ArcShape(_pt_parse(c, d), _f(r, d), _f(a0, d), _f(a1, d))
    raise ValueError(f"неизвестная форма {kind!r}")


def import_fixtures_csv(patch: Patch, text: str) -> CsvImportResult:
    """Строка с ``id`` существующей фикстуры обновляет её; ошибки строк копятся, импорт продолжается."""
    d = detect_dialect(text)
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=d.delimiter)
    created: list[str] = []
    updated: list[str] = []
    errors: list[CsvRowError] = []
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        return CsvImportResult(patch, (), (), (CsvRowError(1, missing[0], "нет обязательной колонки"),))
    for n, row in enumerate(reader, start=2):
        row = {k: (v or "").strip() for k, v in row.items() if k is not None}
        try:
            patch, fid, was_new = _import_row(patch, row, d)
        except (OperationError, ValueError, KeyError) as e:
            col = e.args[0] if isinstance(e, KeyError) and isinstance(e.args[0], str) and e.args[0] in FIXTURE_COLUMNS else ""
            errors.append(CsvRowError(n, col, str(e)))
            continue
        (created if was_new else updated).append(fid)
    return CsvImportResult(patch, tuple(created), tuple(updated), tuple(errors))


def _import_row(patch: Patch, row: dict, d: CsvDialect) -> tuple[Patch, str, bool]:
    for c in REQUIRED:
        if not row.get(c):
            raise ValueError(f"пустая обязательная колонка {c!r}")
    get_fixture_type(patch, row["fixture_type"])
    shape = _parse_shape(row, d)
    out_id = row.get("output") or None
    port_id = row.get("port") or None
    if out_id:
        out = get_output(patch, out_id)
        if port_id and all(p.id != port_id for p in out.ports):
            raise KeyError(f"порт не найден: {port_id}")
    mode = row.get("address_mode") or ("auto" if out_id else "unassigned")
    uni = int(row["universe"]) if row.get("universe") else None
    ch = int(row["channel"]) if row.get("channel") else None
    if mode == "manual" and (uni is None or ch is None or out_id is None):
        raise ValueError("ручной адрес требует output, universe и channel")
    address = Address(mode=AddressMode(mode), output_id=out_id, port_id=port_id, universe=uni, channel=ch,
                      channel_offset=int(row.get("channel_offset") or 0))
    groups = tuple(g for g in row.get("groups", "").split("|") if g)
    fields = dict(name=row["name"], type_id=row["fixture_type"], shape=shape, groups=groups,
                  direction=Direction(row.get("direction") or "forward"), cell_offset=int(row.get("cell_offset") or 0),
                  disabled=frozenset(int(i) for i in row.get("disabled", "").split("|") if i),
                  every_n=int(row.get("every_n") or 1), address=address,
                  length_mm=_f(row["length_mm"], d) if row.get("length_mm") else None,
                  acceptance=Acceptance(row.get("status") or "none"))
    fid = row.get("id") or ""
    exists = fid and any(f.id == fid for f in patch.fixtures)
    if exists:
        p = ops.update_fixture(patch, fid, **fields)
        p = _sync_groups(p, fid, groups)
        return p, fid, False
    if not fid:
        fid = ops.next_ids(patch, "fixture")[0]
    fx = Fixture(id=fid, **fields)
    p = ops.add_fixture(patch, fx)
    return _sync_groups(p, fid, groups), fid, True


def _sync_groups(patch: Patch, fid: str, groups: tuple[str, ...]) -> Patch:
    for g in patch.groups:
        want = g.id in groups
        has = fid in g.fixture_ids
        if want and not has:
            patch = ops.update_group(patch, g.id, fixture_ids=g.fixture_ids + (fid,))
        elif has and not want:
            patch = ops.update_group(patch, g.id, fixture_ids=tuple(i for i in g.fixture_ids if i != fid))
    return patch
