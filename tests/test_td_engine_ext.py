"""find_core_dir: поиск src/core вверх по дереву от папки проекта (dsm_engine_ext, без TD)."""
import importlib.util
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def ext_module():
    # модуль обращается к глобалу ``project`` только внутри функций — импортируем как обычный файл
    spec = importlib.util.spec_from_file_location("dsm_engine_ext_test", os.path.join(ROOT, "src", "td", "dsm_engine_ext.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_td_folder(ext_module):
    assert ext_module.find_core_dir(os.path.join(ROOT, "td")) == os.path.join(ROOT, "src", "core")


def test_td_stand_folder_two_levels_up(ext_module):
    assert ext_module.find_core_dir(os.path.join(ROOT, "td", "stand")) == os.path.join(ROOT, "src", "core")


def test_trailing_slash_and_root_itself(ext_module):
    assert ext_module.find_core_dir(ROOT + os.sep) == os.path.join(ROOT, "src", "core")


def test_not_found(ext_module, tmp_path):
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert ext_module.find_core_dir(str(deep)) == ""


def test_src_core_without_package_is_skipped(ext_module, tmp_path):
    (tmp_path / "src" / "core").mkdir(parents=True)
    assert ext_module.find_core_dir(str(tmp_path / "td")) == ""
