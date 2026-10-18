"""11.1, 11.2: ядро — синтаксис Python 3.11 и только стандартная библиотека.

pixel-engine 10.5: модули src/td — тоже Python 3.11; dsm_geometry без td и numpy."""
import ast
import pathlib
import sys

CORE = pathlib.Path(__file__).resolve().parents[1] / "src" / "core" / "dsm_core"
TD_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "td"
STDLIB = set(sys.stdlib_module_names)
FORBIDDEN = {"td", "numpy", "jsonschema", "pydantic"}


def _modules():
    files = sorted(CORE.glob("*.py"))
    assert files, "в src/core/dsm_core нет модулей"
    return files


def test_parses_as_python_311():
    for f in _modules() + sorted(TD_SRC.glob("*.py")):
        ast.parse(f.read_text(encoding="utf-8"), filename=str(f), feature_version=(3, 11))


def test_only_stdlib_imports():
    for f in _modules():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                top = n.split(".")[0]
                assert top not in FORBIDDEN, f"{f.name}: запрещённый импорт {n}"
                assert top in STDLIB or top == "dsm_core", f"{f.name}: не stdlib: {n}"


def test_package_importable():
    import dsm_core  # noqa: F401
    assert dsm_core.__version__
