import pytest

from dsm_core.io_json import new_patch
from dsm_core.ops import add_fixture_type
from dsm_core.presets import FIXTURE_PRESETS, PORT_PRESETS, preset_fixture_type
from dsm_core.schema import to_dict, validate_document


def test_every_preset_passes_schema():
    p = new_patch()
    for ft in FIXTURE_PRESETS:
        p = add_fixture_type(p, ft)
    assert validate_document(to_dict(p)) == []
    assert len(FIXTURE_PRESETS) == 7


def test_bar_channels():
    bar = preset_fixture_type("bar_common", "ft9")
    assert bar.id == "ft9"
    assert bar.total_channels == 2 + 8 * 3 + 1
    with pytest.raises(KeyError):
        preset_fixture_type("nope", "x")


def test_port_presets_have_limits():
    for name, lim in PORT_PRESETS.items():
        assert lim["max_cells"] >= 1 and lim["max_universes"] >= 1, name
