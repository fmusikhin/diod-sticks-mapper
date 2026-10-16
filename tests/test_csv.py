from conftest import empty_like, make_patch, rgb_type
from dsm_core.addressing import auto_assign_all
from dsm_core.csv_io import RU, CsvDialect, detect_dialect, export_fixtures_csv, import_fixtures_csv, rows_to_csv
from dsm_core.model import Group, RectShape, get_fixture
from dsm_core import ops
from dsm_core.schema import to_dict, validate_document
from dsm_core.tables import address_table


def test_roundtrip_sticks(sticks_patch):
    p = auto_assign_all(sticks_patch)[0]
    p = ops.add_group(p, Group(id="grp1", name="g", fixture_ids=("fx1", "fx2")))
    text = export_fixtures_csv(p)
    assert text.splitlines()[0].startswith("id,name,fixture_type,groups,output,port,universe,channel")
    res = import_fixtures_csv(empty_like(p), text)
    assert res.errors == () and len(res.created) == 8 and res.updated == ()
    assert validate_document(to_dict(res.patch)) == []
    for f in p.fixtures:
        g = get_fixture(res.patch, f.id)
        assert g.address == f.address and g.shape == f.shape and g.groups == f.groups
    assert address_table(res.patch) == address_table(p)


def test_update_by_id_and_errors(sticks_patch):
    p = auto_assign_all(sticks_patch)[0]
    text = export_fixtures_csv(p)
    lines = text.splitlines()
    lines[1] = lines[1].replace("fx1,fx1,ft1", "fx1,Левый стик,ft1")
    lines.append(lines[2].replace("fx2,fx2,ft1", ",Новый,nope"))
    lines.append(lines[2].replace("fx2,fx2,ft1", ",Ещё,ft1"))
    res = import_fixtures_csv(p, "\n".join(lines) + "\n")
    assert get_fixture(res.patch, "fx1").name == "Левый стик"
    assert len(res.updated) == 8 and res.created == ("fx9",)
    assert len(res.errors) == 1 and res.errors[0].line == 10 and "nope" in res.errors[0].reason


def test_russian_excel_dialect(sticks_patch):
    p = auto_assign_all(sticks_patch)[0]
    p = ops.update_fixture(p, "fx1", shape=RectShape((10.5, 20.0), (100.0, 3.5), 15.0))
    ru = export_fixtures_csv(p, RU)
    assert detect_dialect(ru) == RU and '"10,5 20;100 3,5;15"' in ru
    assert detect_dialect(export_fixtures_csv(p)) == CsvDialect()
    res = import_fixtures_csv(empty_like(p), "﻿" + ru)
    assert res.errors == () and get_fixture(res.patch, "fx1").shape == RectShape((10.5, 20.0), (100.0, 3.5), 15.0)


def test_rows_to_csv_dialects():
    rows = [["a", "b"], ["1.5", "x.y"]]
    assert rows_to_csv(rows) == "a,b\n1.5,x.y\n"
    assert rows_to_csv(rows, RU) == "a;b\n1,5;x.y\n"


def test_missing_required_column():
    res = import_fixtures_csv(make_patch(), "id,name\n1,x\n")
    assert res.errors[0].column == "fixture_type" and res.created == ()
