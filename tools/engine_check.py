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
        run_perf()
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
    td.run(f"op('{DSM}').RefreshMaps(True, 90)", delayFrames=20)   # карты активны только по запросу
    _later("_check_map", SETTLE_FRAMES + 30)


def _check_map() -> None:
    dsm = op(DSM)  # noqa: F821
    ext = dsm.ext.DsmEngineExt
    api, patch = ext.api, ext.patch
    name = _s["current"]
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
    if missing and _s.get("map_tries", 0) < 3:          # карта реплик заполняется не за один кук — подождать ещё
        _s["map_tries"] = _s.get("map_tries", 0) + 1
        td.run(f"op('{DSM}').RefreshMaps(True, 60)", delayFrames=1)
        _later("_check_map", 40)
        return
    _s["map_tries"] = 0
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


# ---------------------------------------------------------------- режим perf (6.2)

PERF_SIZES = (14400, 20000, 50000)
PERF_FRAMES = 120
STICK_CELLS = 144


def synthetic_patch(api, n_cells: int):
    """Стики по 144 px GRB на 8 выходах Art-Net 127.0.0.1–8, линии на холсте 1280×720."""
    from dsm_core.model import Address, AddressMode, Fixture, FixtureKind, FixtureType, LineShape, Output, TargetKind
    from dsm_core.protocol import Protocol
    n = max(1, round(n_cells / STICK_CELLS))
    p = api.new_patch()
    p = api.add_fixture_type(p, FixtureType(id="ft1", name="Стик 144", kind=FixtureKind.STICK, cells=STICK_CELLS, color_order="GRB"))
    for k in range(8):
        p = api.add_output(p, Output(id=f"out{k + 1}", name=f"Контроллер {k + 1}", protocol=Protocol.ARTNET, start_universe=k * 64,
                                     ip=f"127.0.0.{k + 1}", target_kind=TargetKind.UNICAST))
    cols = 40
    for i in range(n):
        x = 20.0 + (i % cols) * 31.0
        y = 20.0 + (i // cols) * 70.0
        p = api.add_fixture(p, Fixture(id=f"fx{i + 1}", name=f"Стик {i + 1}", type_id="ft1", shape=LineShape(p1=(x, y), p2=(x, y + 60.0)),
                                       address=Address(mode=AddressMode.AUTO, output_id=f"out{i % 8 + 1}")))
    p, _ = api.auto_assign_all(p)
    return p, n


def run_perf() -> None:
    dsm = op(DSM)  # noqa: F821
    _s.clear()
    _s.update(mode="perf", t0=time.time(), results={}, log=[], sizes=list(PERF_SIZES), phase=None)
    _s["saved"] = dict(patch=dsm.par.Patchfile.eval(), active=dsm.par.Active.eval(), pixelschop=dsm.par.Pixelschop.eval(),
                       src=op("/project1/dsm_test_in").par.top.val, realtime=project.realTime)  # noqa: F821
    st = op(STAND)  # noqa: F821
    ramp = st.op("test_ramp")
    if ramp is None:
        ramp = st.create(constantTOP, "test_ramp")  # noqa: F821
        for c in ("colorr", "colorg", "colorb"):
            ramp.par[c].expr = "(absTime.frame % 100) / 100.0"
    op("/project1/dsm_test_in").par.top = "stand/test_ramp"       # вход меняется каждый кадр → цепочка кукается каждый кадр  # noqa: F821
    dsm.par.Active = True
    _perf_next_size()


def _perf_next_size() -> None:
    dsm = op(DSM)  # noqa: F821
    if not _s["sizes"]:
        _perf_finish()
        return
    n = _s["sizes"].pop(0)
    api = dsm.ext.DsmEngineExt.api
    p, sticks = synthetic_patch(api, n)
    path = os.path.join(_reports_dir(), f"perf_{n}.json")
    api.save(p, path)
    t0 = time.perf_counter()
    ok = dsm.ext.DsmEngineExt.LoadPatch("stand/reports/" + os.path.basename(path))
    reload_s = time.perf_counter() - t0
    cells = len(dsm.op("tables/geo_cells").rows()) - 1
    _s["current"] = dict(n=n, sticks=sticks, cells=cells, reload_s=round(reload_s, 3), loaded=ok, passes=[])
    _s["results"][str(n)] = _s["current"]
    _log(f"perf {n}: {sticks} стиков, {cells} ячеек, Reload {reload_s:.2f} с")
    dsm.par.Pixelschop = False
    _s["pass"] = "chop_off"
    _later("_perf_begin_pass", SETTLE_FRAMES + 60)      # карты активны ~40 кадров после загрузки — замер после


def _perf_begin_pass() -> None:
    dsm = op(DSM)  # noqa: F821
    dsm.ext.DsmEngineExt.RefreshMaps(False)       # карты не участвуют в замере
    dsm.ext.DsmEngineExt.TuneBypass()             # реплики уже есть — флаги обхода точно применены
    ops = [o for o in dsm.findChildren() if o.allowCooking and not o.isCOMP]
    _s["ops"] = ops
    project.realTime = False  # noqa: F821
    _s["m"] = dict(t0=time.perf_counter(), f0=int(absTime.frame), frames=0, cpu=0.0, gpu=0.0, diag_cpu=0.0, diag_gpu=0.0,  # noqa: F821
                   per_op={}, py_cooks0=_python_cooks(), out_cooks0=sum(o.totalCooks for o in dsm.findChildren() if o.OPType == "dmxoutPOP"))
    _later("_perf_tick", 1)


def _python_cooks() -> int:
    dsm = op(DSM)  # noqa: F821
    return sum(o.totalCooks for o in dsm.findChildren() if o.isDAT and o.OPType in ("executeDAT", "parameterexecuteDAT", "datexecuteDAT", "chopexecuteDAT"))


def _perf_tick() -> None:
    m = _s["m"]
    frame = int(absTime.frame)  # noqa: F821
    cpu = gpu = dcpu = dgpu = 0.0
    for o in _s["ops"]:
        if o.cookAbsFrame == frame:
            c, g = o.cpuCookTime, o.gpuCookTime
            diag = "/dsm/diag/" in o.path or "/dsm/sel_" in o.path or o.name in ("out1", "out2", "out3", "out4", "out5")
            if diag:
                dcpu += c; dgpu += g
            else:
                cpu += c; gpu += g
            acc = m["per_op"].setdefault(o.path, [0.0, 0.0, 0])
            acc[0] += c; acc[1] += g; acc[2] += 1
    m["cpu"] += cpu; m["gpu"] += gpu; m["diag_cpu"] += dcpu; m["diag_gpu"] += dgpu; m["frames"] += 1
    if m["frames"] < PERF_FRAMES:
        _later("_perf_tick", 1)
        return
    project.realTime = _s["saved"]["realtime"]  # noqa: F821
    dsm = op(DSM)  # noqa: F821
    dt = time.perf_counter() - m["t0"]
    fr = m["frames"]
    top = sorted(((k, v[0] / fr, v[1] / fr, v[2]) for k, v in m["per_op"].items()), key=lambda x: -(x[1] + x[2]))[:8]
    res = dict(pass_=_s["pass"], frames=fr, seconds=round(dt, 2), fps=round(fr / dt, 1), cpu_ms=round(m["cpu"] / fr, 3), gpu_ms=round(m["gpu"] / fr, 3),
               total_ms=round((m["cpu"] + m["gpu"]) / fr, 3), diag_ms=round((m["diag_cpu"] + m["diag_gpu"]) / fr, 3),
               python_cooks=_python_cooks() - m["py_cooks0"],
               out_cooks=sum(o.totalCooks for o in dsm.findChildren() if o.OPType == "dmxoutPOP") - m["out_cooks0"],
               top=[(k.split("/dsm/")[-1], round(c, 3), round(g, 3), n) for k, c, g, n in top])
    _s["current"]["passes"].append(res)
    _log(f"perf {_s['current']['n']} [{res['pass_']}]: движок {res['total_ms']} мс/кадр (cpu {res['cpu_ms']} + gpu {res['gpu_ms']}), "
         f"диагностика {res['diag_ms']} мс, {res['fps']} fps, Python-куков {res['python_cooks']}, куков вывода {res['out_cooks']}")
    if _s["pass"] == "chop_off":
        _s["pass"] = "chop_on"
        dsm.par.Pixelschop = True
        _later("_perf_begin_pass", 10)
    else:
        dsm.par.Pixelschop = False
        _later("_perf_next_size", 10)


def _perf_finish() -> None:
    dsm = op(DSM)  # noqa: F821
    sv = _s["saved"]
    project.realTime = sv["realtime"]  # noqa: F821
    dsm.par.Active = sv["active"]; dsm.par.Pixelschop = sv["pixelschop"]
    op("/project1/dsm_test_in").par.top = sv["src"] or "stand/test_src"  # noqa: F821
    dsm.par.Patchfile = sv["patch"]; dsm.ext.DsmEngineExt.Reload()
    report = dict(mode="perf", frames=PERF_FRAMES, seconds=round(time.time() - _s["t0"], 1), results=_s["results"], log=_s["log"])
    path = os.path.join(_reports_dir(), f"perf_{time.strftime('%Y%m%d_%H%M%S')}.json")
    json.dump(report, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    _log(f"perf ИТОГ: отчёт {path}")
    _s["done"] = True
