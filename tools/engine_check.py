"""engine_check — стенд спеки pixel-engine. Исполняется ВНУТРИ TouchDesigner.

Подключение: textDAT ``/project1/stand/run_check`` синхронизирован с этим файлом (par.file относительно папки
проекта); запуск из textport или MCP::

    op('/project1/stand/run_check').module.start('examples')   # карта DMX vs ядро + перехват байт в байт
    op('/project1/stand/run_check').module.start('perf')       # стоимость кадра на 14 400 / 20 000 / 50 000 ячеек
    op('/project1/stand/run_check').module.start('smoke')      # дым-тест .tox (CONTRIBUTING §5)

(имя ``start``, а не ``run`` — ``run`` занято встроенной функцией TD для отложенного исполнения).

Шаги идут по кадрам (``run(..., delayFrames=N)``): карты DMX и реплики заполняются не за один кук, а перехват
``tools/dmx_listen.py`` работает подпроцессом ``sys.executable`` (Python из поставки TD). Отчёт — JSON в
``td/stand/reports/`` (в .gitignore) и печать «совпало / расхождения» в textport.

Ожидание байтов считается из ядра: вход — константный серый GREY (constantTOP ``stand/test_grey``), значение
канала = floor(float32(GREY · brightness · color) · 255) (правило квантования — спайк 1.1), head/tail/cell_extra —
``default`` профиля, неактивные ячейки (disabled / every_n) — 0, каналы 511–512 вне ячеек — 0.
"""
from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import sys
import time

import td

DSM = "/project1/dsm"
STAND = "/project1/stand"
GREY = 0.6
CAPTURE_SECONDS = 2.0
SETTLE_FRAMES = 45          # реплики, карты, кэши после загрузки патча
CAPTURE_FRAMES = 160        # ~2,5 с при 60 fps: перехват + запас

# Состояние прогона живёт на модуле td: модуль DAT пересоздаётся между отложенными шагами, а td — нет.
_s: dict = td.__dict__.setdefault("_dsm_engine_check", {})


# ---------------------------------------------------------------- служебное

def _repo_root() -> str:
    return os.path.normpath(os.path.join(project.folder, ".."))  # noqa: F821 — глобалы TD


def _reports_dir() -> str:
    p = os.path.join(project.folder, "stand", "reports")  # noqa: F821
    os.makedirs(p, exist_ok=True)
    return p


def f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


def quantize8(v: float) -> int:
    """Байт из нормированного значения как в dmxfixturePOP: floor(float32(v)·255), с зажимом 0–255."""
    v = min(max(f32(v), 0.0), 1.0)
    return int(math.floor(f32(v * 255.0)))


def quantize16(v: float) -> tuple[int, int]:
    v = min(max(f32(v), 0.0), 1.0)
    n = int(math.floor(f32(v * 65535.0)))
    return n >> 8, n & 0xFF


def _later(fn_name: str, frames: int) -> None:
    td.run(f"op('{STAND}/run_check').module.{fn_name}()", delayFrames=frames)


def _log(msg: str) -> None:
    print(f"[engine_check] {msg}")
    _s.setdefault("log", []).append(msg)


# ---------------------------------------------------------------- ожидание ядра

def expected_bytes(api, patch, grey: float, master: float = 1.0) -> dict[tuple[str, int], list[int]]:
    """(ip, юниверс) → 512 байт, как их должен отправить движок при константном сером входе."""
    types = {t.id: t for t in patch.fixture_types}
    outputs = {o.id: o for o in patch.outputs}
    routing = api.routing_table(patch)
    col = {n: k for k, n in enumerate(routing[0])}
    ip_of = {(r[col["output_id"]], int(r[col["universe"]])): r[col["ip"]] for r in routing[1:]}
    exp: dict[tuple[str, int], list[int]] = {}
    for key in ip_of:
        exp[(ip_of[key], key[1])] = [0] * 512

    def put(out_id: str, universe: int, channel: int, value: int) -> None:
        from dsm_core.model import effective_target_ip
        ip = ip_of.get((out_id, universe), effective_target_ip(outputs[out_id]))
        exp.setdefault((ip, universe), [0] * 512)[channel - 1] = value

    for fx in patch.fixtures:
        ft = types.get(fx.type_id)
        if ft is None or fx.shape.kind not in ("line", "polyline", "rect"):
            continue
        segs = api.fixture_segments(patch, fx)
        if not segs:
            continue
        out_id = fx.address.output_id
        cells = api.cell_order(ft, fx)
        cpc = ft.channels_per_cell
        placed: set[int] = set()
        for sg in segs:
            if sg.kind == "cells":
                for k in range(sg.n_cells):
                    di = sg.first_cell + k
                    if di in placed:
                        continue
                    placed.add(di)
                    c = cells[di]
                    on = c.active and not fx.mute
                    ch = sg.first_channel + k * cpc
                    vals: list[int] = []
                    for letter in ft.color_order:
                        idx = {"R": 0, "G": 1, "B": 2}.get(letter)
                        v = grey * master * fx.brightness * fx.color[idx] if (on and idx is not None) else 0.0
                        if ft.bits == 16:
                            vals.extend(quantize16(v))
                        else:
                            vals.append(quantize8(v))
                    for chn in ft.cell_extra.channels:
                        vals.extend((chn.default, 0) if chn.bits == 16 else (chn.default,))
                    for j, v in enumerate(vals):
                        put(out_id, sg.universe, ch + j, v)
            else:
                block = ft.head if sg.kind == "head" else ft.tail
                vals = []
                for chn in block.channels:
                    vals.extend((chn.default, 0) if chn.bits == 16 else (chn.default,))
                for j, v in enumerate(vals[: sg.last_channel - sg.first_channel + 1]):
                    put(out_id, sg.universe, sg.first_channel + j, v)
    return exp


# ---------------------------------------------------------------- режим examples

def start(mode: str = "examples") -> None:
    dsm = op(DSM)  # noqa: F821
    if mode == "examples":
        _s.clear()
        queue = ["../examples/" + f for f in sorted(os.listdir(os.path.join(_repo_root(), "examples"))) if f.endswith(".json")]
        queue += ["stand/" + f for f in sorted(os.listdir(os.path.join(project.folder, "stand")))  # noqa: F821
                  if f.endswith(".json") and f not in ("delay_test.json",)]
        _s.update(mode=mode, t0=time.time(), results={}, queue=queue)
        _s["saved"] = dict(patch=dsm.par.Patchfile.eval(), active=dsm.par.Active.eval(), master=dsm.par.Master.eval(),
                           src=op("/project1/dsm_test_in").par.top.val or "stand/test_src")  # noqa: F821
        if _s["saved"]["src"] == "stand/test_grey":
            _s["saved"]["src"] = "stand/test_src"
        st = op(STAND)  # noqa: F821
        grey = st.op("test_grey") or st.create(constantTOP, "test_grey")  # noqa: F821
        grey.par.colorr = grey.par.colorg = grey.par.colorb = GREY
        grey.par.outputresolution = "custom"; grey.par.resolutionw = 64; grey.par.resolutionh = 64
        grey.nodeX, grey.nodeY = -100, -400; grey.viewer = True
        op("/project1/dsm_test_in").par.top = "stand/test_grey"  # noqa: F821
        dsm.par.Master = 1.0; dsm.par.Active = False
        _log(f"examples: {_s['queue']}")
        _next_example()
    elif mode == "perf":
        from_perf = globals().get("run_perf")
        if from_perf:
            from_perf()
    elif mode == "smoke":
        from_smoke = globals().get("run_smoke")
        if from_smoke:
            from_smoke()
    else:
        raise ValueError(f"неизвестный режим {mode!r}")


def _next_example() -> None:
    if not _s["queue"]:
        _finish_examples()
        return
    name = _s["queue"].pop(0)
    _s["current"] = name
    dsm = op(DSM)  # noqa: F821
    ok = dsm.ext.DsmEngineExt.LoadPatch(name)
    _s["results"][name] = {"loaded": ok, "status": dsm.par.Status.eval()}
    _log(f"{name}: загрузка {'ok' if ok else 'ОШИБКА: ' + dsm.ext.DsmEngineExt.message}")
    _later("_check_map", SETTLE_FRAMES)


def _check_map() -> None:
    dsm = op(DSM)  # noqa: F821
    ext = dsm.ext.DsmEngineExt
    api, patch = ext.api, ext.patch
    name = _s["current"]
    for o in dsm.op("fixtures").findChildren():
        if o.OPType == "dmxmapDAT" and o.allowCooking:
            o.par.updatevalpulse.pulse()
    m = dsm.op("diag/dmxmap_all")
    m.cook(force=True)
    exp = expected_bytes(api, patch, GREY)
    # карта: (universe, channel) → value у занятых каналов
    got: dict[tuple[int, int], int] = {}
    got_ip: dict[tuple[int, int], str] = {}
    for r in m.rows()[1:]:
        if r[6].val and r[4].val.isdigit():           # mergeDAT склеивает карты вместе с заголовками
            got[(int(r[0].val), int(r[4].val))] = int(r[5].val)
            got_ip[(int(r[0].val), int(r[4].val))] = r[11].val
    # занятые каналы по ядру: cell_table + head/tail из segment_table
    occupied: dict[tuple[int, int], int] = {}
    cells = api.cell_table(patch)[1:]
    segs = api.segment_table(patch)[1:]
    for r in cells:
        if r[3] and r[4]:
            for k in range(int(r[5])):
                occupied[(int(r[3]), int(r[4]) + k)] = 1
    for r in segs:
        if r[4] in ("head", "tail"):
            for ch in range(int(r[2]), int(r[3]) + 1):
                occupied[(int(r[1]), ch)] = 1
    missing = sorted(set(occupied) - set(got))
    extra = sorted(set(got) - set(occupied))
    # значения карты против ожидания: ключ — (адрес назначения, юниверс), как в ожидании ядра
    value_mismatch = [(u, ch, exp.get((got_ip[(u, ch)], u), [0] * 512)[ch - 1], v) for (u, ch), v in sorted(got.items())
                      if exp.get((got_ip[(u, ch)], u), [0] * 512)[ch - 1] != v]
    res = _s["results"][name]
    res["map"] = dict(expected_channels=len(occupied), map_channels=len(got), missing=missing[:10], extra=extra[:10],
                      value_mismatch=value_mismatch[:10], value_mismatch_count=len(value_mismatch),
                      ok=(not missing and not extra and not value_mismatch))
    _log(f"{name}: карта DMX — {'совпало' if res['map']['ok'] else 'РАСХОЖДЕНИЯ'} "
         f"({len(got)}/{len(occupied)} каналов, значений не совпало {len(value_mismatch)})")
    _s["expected"] = {f"{ip}|{uni}": data for (ip, uni), data in exp.items()}
    _start_capture()


def _start_capture() -> None:
    dsm = op(DSM)  # noqa: F821
    name = _s["current"]
    exp = _s["expected"]
    ips_by_proto: dict[str, set[str]] = {"artnet": set(), "sacn": set()}
    patch = dsm.ext.DsmEngineExt.patch
    proto_of = {o.id: o.protocol.value for o in patch.outputs}
    api = dsm.ext.DsmEngineExt.api
    routing = api.routing_table(patch)
    col = {n: k for k, n in enumerate(routing[0])}
    multicast = set()
    nonlocal_ips = set()
    for r in routing[1:]:
        ip = r[col["ip"]]
        if ip.startswith("239."):
            multicast.add(ip)
            ips_by_proto["sacn"].add("0.0.0.0")
        elif ip.startswith("127."):
            ips_by_proto[proto_of[r[col["output_id"]]]].add(ip)
        else:
            nonlocal_ips.add(ip)          # недоступный/внешний адрес: перехватить нельзя, проверяется только карта
    _s["nonlocal"] = sorted(nonlocal_ips)
    procs = {}
    tool = os.path.join(_repo_root(), "tools", "dmx_listen.py")
    for proto, ips in ips_by_proto.items():
        if not ips:
            continue
        out = os.path.join(_reports_dir(), f"cap_{proto}.json")
        if os.path.exists(out):
            os.remove(out)                # иначе при сбое перехватчика сравним со старым файлом
        args = [sys.executable, "-I", tool, proto, str(CAPTURE_SECONDS), out, "--bind", ",".join(sorted(ips))]
        if proto == "sacn" and multicast:
            args += ["--mcast", ",".join(sorted(multicast))]
        procs[proto] = (subprocess.Popen(args, cwd=_repo_root()), out)
    _s["procs"] = procs
    _s["multicast"] = sorted(multicast)
    dsm.par.Active = True
    _later("_finish_capture", CAPTURE_FRAMES)


def _finish_capture() -> None:
    dsm = op(DSM)  # noqa: F821
    dsm.par.Active = False
    name = _s["current"]
    res = _s["results"][name]
    exp = _s["expected"]
    patch = dsm.ext.DsmEngineExt.patch
    proto_of_ip: dict[str, str] = {}
    for o in patch.outputs:
        ip = o.ip or ("239." if o.target_kind.value == "multicast" else "127.0.0.1")
        proto_of_ip[ip] = o.protocol.value
    caps: dict[str, dict] = {}
    for proto, (p, out) in _s["procs"].items():
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
        caps[proto] = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else {"dst": {}, "error": "нет файла перехвата"}
    seen = skipped = 0
    mism: list = []
    for key, data in exp.items():
        ip, uni = key.split("|")
        if ip in _s.get("nonlocal", []):
            skipped += 1
            continue
        proto = "sacn" if ip.startswith("239.") else proto_of_ip.get(ip, "artnet")
        dst = caps.get(proto, {}).get("dst", {})
        unis = dst.get("0.0.0.0" if ip.startswith("239.") else ip, {}).get("universes", {})
        got = unis.get(uni)
        if got is None:
            if ip.startswith("239."):
                skipped += 1      # multicast на loopback не доставляется — проверяется unicast-вариантом патча
            continue
        seen += 1
        gd = got["data"] + [0] * (512 - len(got["data"]))
        for ch in range(512):
            if gd[ch] != data[ch]:
                mism.append((ip, int(uni), ch + 1, data[ch], gd[ch]))
    rates = {f"{proto} {ip}:{u}": v["rate_hz"] for proto, c in caps.items() for ip, d in c.get("dst", {}).items()
             for u, v in d.get("universes", {}).items()}
    artsync = {f"{proto} {ip}": d.get("artsync", 0) for proto, c in caps.items() for ip, d in c.get("dst", {}).items()}
    res["capture"] = dict(universes_seen=seen, skipped=skipped, expected=len(exp), mismatches=len(mism), first=mism[:10],
                          rates=rates, artsync=artsync, errors=[c["error"] for c in caps.values() if "error" in c])
    res["expected_universes"] = len(exp)
    res["bytes_ok"] = seen + skipped == len(exp) and not mism and not res["capture"]["errors"]
    note = f", пропущено (multicast/не локальные) {skipped}" if skipped else ""
    _log(f"{name}: перехват — {'совпало байт в байт' if res['bytes_ok'] else 'РАСХОЖДЕНИЯ'} "
         f"(юниверсов {seen}/{len(exp)}{note}, байт не совпало {len(mism)})")
    _later("_next_example", 5)


def _finish_examples() -> None:
    dsm = op(DSM)  # noqa: F821
    sv = _s["saved"]
    op("/project1/dsm_test_in").par.top = sv["src"]  # noqa: F821
    dsm.par.Master = sv["master"]; dsm.par.Active = sv["active"]
    dsm.par.Patchfile = sv["patch"]
    dsm.ext.DsmEngineExt.Reload()
    report = dict(mode="examples", grey=GREY, seconds=round(time.time() - _s["t0"], 1), results=_s["results"], log=_s["log"])
    path = os.path.join(_reports_dir(), f"examples_{time.strftime('%Y%m%d_%H%M%S')}.json")
    json.dump(report, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    allok = all(r.get("map", {}).get("ok") and r.get("bytes_ok") for r in _s["results"].values())
    _log(f"ИТОГ: {'совпало' if allok else 'есть расхождения'}; отчёт {path}")
    _s["done"] = True
    _s["report"] = path
