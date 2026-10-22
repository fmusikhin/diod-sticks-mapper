"""11.6, 11.7: примеры загружаются без ошибок и шлют на 127.0.0.1; в репо нет реальных IP."""
import ipaddress
import pathlib
import re

import pytest

from dsm_core import api

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.json"))
ALLOWED = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("192.0.2.0/24"),
           ipaddress.ip_network("239.255.0.0/16"),
           ipaddress.ip_network("255.255.255.255/32")]  # broadcast — не реальный адрес



@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_loads_clean(path):
    patch, notes = api.load(path)
    issues = api.validate(patch)
    assert [i for i in issues if i.level == api.IssueLevel.ERROR] == []
    rt = api.routing_table(patch)
    assert all(r[6] == "127.0.0.1" or r[6].startswith("239.255.") for r in rt[1:])
    assert api.dumps(patch) == path.read_text(encoding="utf-8")  # сохранённый файл детерминирован
    assert api.summary(patch)["cells"] > 0


def test_four_examples_present():
    assert {p.stem for p in EXAMPLES} == {"strip_ws2812b", "sticks_8x_artnet", "matrix_16x16_sacn", "bar_common_channels"}


def test_no_real_ips_in_repo():
    ip_re = re.compile(r"(?<![\d.])(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?![\d.])")
    bad = []
    for folder in ("examples", "tests", "src/core", "src/td", "td/stand"):
        for f in (ROOT / folder).rglob("*"):
            if f.suffix not in (".py", ".json", ".csv", ".md", ".txt") or "reports" in f.parts:
                continue      # td/stand/reports — отчёты и перехваты стенда, не в git
            for m in ip_re.finditer(f.read_text(encoding="utf-8", errors="ignore")):
                try:
                    ip = ipaddress.ip_address(m.group(1))
                except ValueError:
                    continue
                if not any(ip in n for n in ALLOWED):
                    bad.append(f"{f.relative_to(ROOT)}: {ip}")
    assert bad == []
