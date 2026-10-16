import json

import pytest

from conftest import fixture, make_patch, output, rgb_type
from dsm_core.errors import PatchError, SchemaError
from dsm_core.io_json import dumps, load, loads, new_patch, save
from dsm_core.schema import SCHEMA_VERSION, from_dict, to_dict, validate_document


def minimal_doc():
    return {
        "schema_version": SCHEMA_VERSION,
        "canvas": {"width": 1280, "height": 720, "units": "px"},
        "fixture_types": [{"id": "ft1", "name": "WS2812B", "kind": "stick", "cells": 144, "color_order": "GRB"}],
        "outputs": [{"id": "out1", "name": "Контроллер 1", "protocol": "artnet", "target_kind": "unicast",
                     "ip": None, "start_universe": 0, "fixture_ids": ["fx1"]}],
        "fixtures": [{"id": "fx1", "name": "Стик 1", "type_id": "ft1",
                      "shape": {"kind": "line", "p1": [100, 100], "p2": [100, 600]},
                      "address": {"mode": "auto", "output_id": "out1", "universe": 0, "channel": 1}}],
        "groups": [],
    }


def test_new_patch_defaults():
    p = new_patch()
    assert p.canvas.width == 1280 and p.canvas.height == 720 and p.canvas.units == "px"
    assert not p.outputs and not p.fixtures


def test_minimal_doc_roundtrip_and_defaults():
    p = from_dict(minimal_doc())
    fx = p.fixtures[0]
    assert fx.direction.value == "forward" and fx.every_n == 1 and fx.address.channel_offset == 0
    assert p.outputs[0].rules.whole_pixel and not p.outputs[0].rules.skip_511_512
    assert from_dict(to_dict(p)) == p


def test_dumps_deterministic_and_stable(tmp_path):
    p = from_dict(minimal_doc())
    t1 = dumps(p)
    p2, notes = loads(t1)
    assert dumps(p2) == t1
    assert t1.endswith("\n") and "\r" not in t1
    save(p, tmp_path / "a.json")
    raw = (tmp_path / "a.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw
    assert load(tmp_path / "a.json")[0] == p


def test_schema_errors_have_paths():
    doc = minimal_doc()
    doc["fixtures"][0]["address"]["channel"] = 513
    doc["fixture_types"][0]["color_order"] = "RGBX"
    errs = validate_document(doc)
    paths = {e.path for e in errs}
    assert "fixtures[0].address.channel" in paths
    assert "fixture_types[0].color_order" in paths
    with pytest.raises(SchemaError) as ei:
        from_dict(doc)
    assert len(ei.value.errors) == 2


def test_broken_refs_and_lists():
    doc = minimal_doc()
    doc["fixtures"][0]["type_id"] = "nope"
    assert any(e.path == "fixtures[0].type_id" for e in validate_document(doc))
    doc = minimal_doc()
    doc["outputs"][0]["fixture_ids"] = []
    assert any("ровно в одном списке" in e.reason for e in validate_document(doc))


def test_required_fields_and_shapes():
    doc = minimal_doc()
    del doc["fixtures"][0]["shape"]
    assert any(e.path == "fixtures[0].shape" for e in validate_document(doc))
    doc = minimal_doc()
    doc["fixtures"][0]["shape"] = {"kind": "polyline", "points": [[0, 0]]}
    assert any("не меньше 2" in e.reason for e in validate_document(doc))
    doc = minimal_doc()
    doc["fixtures"][0]["shape"] = {"kind": "curve", "points": [[0, 0], [1, 1], [2, 2], [3, 3]]}
    assert validate_document(doc) == []


def test_absolute_background_rejected():
    doc = minimal_doc()
    doc["canvas"]["background"] = "C:/plans/stage.png"
    assert any(e.path == "canvas.background" for e in validate_document(doc))
    doc["canvas"]["background"] = "plans/stage.png"
    assert validate_document(doc) == []


def test_unknown_fields_survive_roundtrip():
    doc = minimal_doc()
    doc["future_field"] = {"x": 1}
    doc["fixtures"][0]["power_limit"] = 42
    p, notes = loads(json.dumps(doc))
    assert p.extra == {"future_field": {"x": 1}} and p.fixtures[0].extra == {"power_limit": 42}
    assert any("неизвестные поля" in n for n in notes)
    out = json.loads(dumps(p))
    assert out["future_field"] == {"x": 1} and out["fixtures"][0]["power_limit"] == 42


def test_newer_schema_rejected():
    doc = minimal_doc()
    doc["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(PatchError, match="новее"):
        loads(json.dumps(doc))


def test_model_patch_to_dict_and_back():
    p = make_patch([rgb_type()], [fixture("fx1")], [output(fixture_ids=("fx1",))])
    assert validate_document(to_dict(p)) == []
    assert from_dict(to_dict(p)) == p
