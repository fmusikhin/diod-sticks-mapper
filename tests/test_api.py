"""10.1, 10.3: фасад отдаёт все операции design; ядро не трогает файлы вне load/save."""
import inspect

from dsm_core import api

EXPECTED = """new_patch load loads save dumps validate_document from_dict to_dict
add_fixture_type update_fixture_type remove_fixture_type add_fixture update_fixture remove_fixture duplicate_fixtures
add_output update_output remove_output add_port update_port remove_port add_group update_group remove_group
set_group_members set_manual_address assign_to_output clear_address set_acceptance fix_reversed next_ids
auto_assign auto_assign_all assign_sequential layout_fixture fixture_segments segments_by_fixture
validate Issue IssueCode IssueLevel
address_table segment_table cell_table routing_table issue_table summary
export_fixtures_csv import_fixtures_csv rows_to_csv detect_dialect
FIXTURE_PRESETS PORT_PRESETS preset_fixture_type
get_fixture get_fixture_type get_output get_group get_port
cell_order cell_count matrix_order to_port_address from_port_address""".split()


def test_facade_exports_everything():
    missing = [n for n in EXPECTED if not hasattr(api, n)]
    assert missing == []
    assert set(EXPECTED) <= set(api.__all__)


def test_docstrings_in_russian():
    for name in ("auto_assign", "validate", "import_fixtures_csv", "layout_fixture", "dumps", "new_patch"):
        doc = inspect.getdoc(getattr(api, name)) or ""
        assert any("а" <= ch <= "я" for ch in doc.lower()), name


def test_no_side_effects_on_input(sticks_patch):
    before = sticks_patch
    p, _ = api.auto_assign_all(sticks_patch)
    assert sticks_patch == before and p is not before
    api.validate(p)
    api.address_table(p)
    assert p == api.auto_assign_all(sticks_patch)[0]
