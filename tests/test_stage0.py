"""11.4, 11.5, 8.3, 6.13: стенд этапа 0 (docs/architecture.md §13) и круговые обходы на примерах."""
import csv
import pathlib

import pytest

from conftest import empty_like
from dsm_core import api

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data"


def _read(name):
    with open(DATA / name, encoding="utf-8", newline="") as f:
        return [row for row in csv.reader(f)]


def test_stage0_tables_match_expected(stage0_patch):
    p, issues = api.auto_assign_all(stage0_patch)
    assert issues == []
    assert api.address_table(p) == _read("stage0_expected_addresses.csv")
    assert api.routing_table(p) == _read("stage0_expected_routing.csv")
    # байт в байт то, что видел DMX Map DAT: канал с 1, 170 + 30, 511–512 пустые, 20 = 0.1.4
    cells = api.cell_table(p)
    fx1 = [r for r in cells[1:] if r[0] == "fx1"]
    assert fx1[169][3:5] == ["20", "508"] and fx1[170][3:5] == ["21", "1"]
    assert api.validate(p) == []


def test_stage0_deterministic(stage0_patch):
    a = api.auto_assign_all(stage0_patch)[0]
    b = api.auto_assign_all(stage0_patch)[0]
    assert a == b and api.dumps(a) == api.dumps(b)
    assert api.auto_assign_all(a)[0] == a


@pytest.mark.parametrize("path", sorted((ROOT / "examples").glob("*.json")), ids=lambda p: p.stem)
def test_examples_roundtrip_json_and_csv(path):
    p, _ = api.load(path)
    text = api.dumps(p)
    p2, _ = api.loads(text)
    assert p2 == p and api.dumps(p2) == text
    res = api.import_fixtures_csv(empty_like(p), api.export_fixtures_csv(p))
    assert res.errors == ()
    assert api.address_table(res.patch) == api.address_table(p)
    assert api.segment_table(res.patch) == api.segment_table(p)
