from __future__ import annotations

import datetime
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
GUI_DIR = ROOT / "gui"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from gateway.gateway import AgentGatewayManager, load_gateway_config, save_gateway_config  # noqa: E402
try:
    from scripts.patch_inkling_thinking import ensure_patched as ensure_inkling_thinking_patched  # noqa: E402
except Exception:
    ensure_inkling_thinking_patched = None

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

CONTROL_DIR = ROOT / "control"
if IS_WIN:
    PYTHON_EXE = CONTROL_DIR / "python" / "python.exe"
    PYTHONW_EXE = CONTROL_DIR / "python" / "pythonw.exe"
    ENGINE_EXE = ROOT / "engine" / "strata-inkling.exe"
else:
    _mac_py = CONTROL_DIR / "python" / "bin" / "python3"
    PYTHON_EXE = _mac_py if _mac_py.is_file() else Path(sys.executable)
    PYTHONW_EXE = PYTHON_EXE
    ENGINE_EXE = ROOT / "engine" / "strata-inkling"

SERVER_SCRIPT = CONTROL_DIR / "serve" / "server.py"
MODELS_DIR = ROOT / "models" / "Inkling-2-Mini-Strata"
LOGS_DIR = ROOT / "logs"
STATE_FILE = LOGS_DIR / "server-process.json"
GUI_STATE_FILE = LOGS_DIR / "gui-process.json"

GUI_HOST = "127.0.0.1"
GUI_PORT = 8088
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0

PACK_CATALOG = {
    "SQ-20": {
        "file": "Inkling-2-Mini-SQ-20.strata",
        "bytes": 19_346_685_952,
        "sha": "0bbfd52344f0dd4779a7807b5f73554a812f24c035e6877b63cd0d499f7e1b97",
        "key": "inkling-2-mini-sq-20",
        "match_pct": "100.00%",
    },
    "SQ-15": {
        "file": "Inkling-2-Mini-SQ-15.strata",
        "bytes": 14_348_316_672,
        "sha": "2ea32eed533afba257e2cbee7b073dc2c3da80fe55d1d2d794779b5498e30737",
        "key": "inkling-2-mini-sq-15",
        "match_pct": "96.68%",
    },
    "SQ-10": {
        "file": "Inkling-2-Mini-SQ-10.strata",
        "bytes": 10_471_526_400,
        "sha": "ac4d452ad03e4930abd9d46f9c5eec22b2f75a1da45a1e49bd24ccd58b065934",
        "key": "inkling-2-mini-sq-10",
        "match_pct": "92.35%",
    },
}

_LOCK = threading.Lock()
_CONFLICT_CACHE: dict = {"ts": 0.0, "data": [], "refreshing": False}
_HW_CACHE: dict | None = None
_HTTPD_REF: list[ThreadingHTTPServer] = []
_GATEWAY_REF: list[AgentGatewayManager] = []


class _FastThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64


def _query_metal_workset_gib() -> float | None:
    if not IS_MAC:
        return None
    try:
        import ctypes
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        metal = ctypes.CDLL("/System/Library/Frameworks/Metal.framework/Metal")
        metal.MTLCreateSystemDefaultDevice.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        dev = metal.MTLCreateSystemDefaultDevice()
        if not dev:
            return None
        msg_u64 = ctypes.CFUNCTYPE(ctypes.c_uint64, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        ws = msg_u64(dev, objc.sel_registerName(b"recommendedMaxWorkingSetSize"))
        return round(ws / (1024 ** 3), 2) if ws else None
    except Exception:
        return None


def _get_gateway() -> AgentGatewayManager:
    if not _GATEWAY_REF:
        _GATEWAY_REF.append(AgentGatewayManager(get_status, start_model, stop_model))
    return _GATEWAY_REF[0]


def _detect_hardware_static() -> dict:
    global _HW_CACHE
    if _HW_CACHE is not None:
        return _HW_CACHE
    vm = psutil.virtual_memory()
    ram_gb = round(vm.total / (1024**3), 1)
    if IS_MAC:
        chip = "Apple Silicon"
        try:
            r = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=2)
            if r.returncode == 0 and r.stdout.strip():
                chip = r.stdout.strip()
        except Exception:
            pass
        is_high_bw = any(tier in chip for tier in ("Max", "Ultra"))
        workset_gib = _query_metal_workset_gib()
        # Empirically tuned on Apple M5 24 GB: base M1-M5 chips get 2.5-2.8x faster CJK & 8K decode
        # with draft=off (25.1-26.2 vs 8.9 tok/s), while Max/Ultra high-BW chips default to dspark.
        _HW_CACHE = {
            "os": "macOS",
            "is_mac": True,
            "is_win": False,
            "backend": "metal",
            "gpu_name": chip,
            "gpu_arch": "Apple Silicon Metal 4 MPP" if "M5" in chip else "Apple Silicon Metal (Unified)",
            "gpu_vram_gb": int(round(ram_gb)),
            "metal_workset_gib": workset_gib,
            "is_high_bw_mac": is_high_bw,
            "unified_memory": True,
            "memory_label": f"{int(round(ram_gb))} GB 統一記憶體" + (f" · Metal 上限 {workset_gib} GiB" if workset_gib else ""),
            "recommended_mode": "dspark" if is_high_bw else "fast",
            "default_kv": "fp8",
            "default_chunk": 1024,
        }
    else:
        gpu_name = "AMD Radeon RX 6700 XT"
        gpu_arch = "RDNA2 (gfx1031)"
        backend = "hip"
        vram_gb = 12
        try:
            build_json = ROOT / "engine" / "BUILD.json"
            if build_json.is_file():
                bj = json.loads(build_json.read_text(encoding="utf-8-sig"))
                if bj.get("variant"):
                    gpu_arch = f"HIP ({bj['variant']} · {bj.get('archs', '')})"
                if bj.get("backend") == "cuda":
                    backend = "cuda"
        except Exception:
            pass
        if IS_WIN:
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'Radeon RX|Radeon AI|NVIDIA|RTX|GTX' } | Select-Object -First 1).Name"],
                    capture_output=True, text=True, errors="ignore", timeout=3, creationflags=CREATE_NO_WINDOW,
                )
                if r.returncode == 0 and r.stdout.strip():
                    gpu_name = r.stdout.strip()
                    if any(k in gpu_name.upper() for k in ("NVIDIA", "RTX", "GTX")):
                        backend = "cuda"
                        gpu_arch = "NVIDIA CUDA"
            except Exception:
                pass
        is_rdna2 = backend == "hip" and ("rdna2" in gpu_arch.lower() or "6700" in gpu_name or "6800" in gpu_name or "6900" in gpu_name)
        _HW_CACHE = {
            "os": "Windows" if IS_WIN else platform.system(),
            "is_mac": False,
            "is_win": IS_WIN,
            "backend": backend,
            "gpu_name": gpu_name,
            "gpu_arch": gpu_arch,
            "gpu_vram_gb": vram_gb,
            "unified_memory": False,
            "memory_label": f"{vram_gb} GB VRAM",
            "recommended_mode": "fast" if is_rdna2 else "dspark",
            "default_kv": "k8v8" if is_rdna2 else "fp8",
            "default_chunk": 512,
        }
    return _HW_CACHE


def _resolve_tokenizer_dir(model_key: str) -> Path:
    tok_dir = ROOT / "packs" / model_key / "tokenizer"
    if tok_dir.is_dir():
        return tok_dir
    for fallback_key in ("inkling-2-mini-sq-10", "inkling-2-mini-sq-15", "inkling-2-mini-sq-20"):
        cand = ROOT / "packs" / fallback_key / "tokenizer"
        if cand.is_dir():
            return cand
    return tok_dir


def list_installed_packs() -> list[dict]:
    """Return status of all Inkling-2-Mini quant packs in PACK_CATALOG."""
    out: list[dict] = []
    for pack_name in ("SQ-10", "SQ-15", "SQ-20"):
        meta = PACK_CATALOG[pack_name]
        fpath = MODELS_DIR / meta["file"]
        installed = fpath.is_file() and fpath.stat().st_size == meta["bytes"]
        out.append({
            "pack": pack_name,
            "model_key": meta["key"],
            "size_gb": round(meta["bytes"] / 1e9, 2),
            "match_pct": meta["match_pct"],
            "installed": installed,
        })
    return out


def detect_installed_model(preferred_pack: str | None = None) -> dict:
    """Detect installed Inkling-2-Mini pack (SQ-10 / SQ-15 / SQ-20) and tokenizer directory."""
    gw_pack = ""
    if _GATEWAY_REF:
        gw_pack = str(_GATEWAY_REF[0].config.get("default_pack") or "").upper()
    else:
        gw_pack = str(load_gateway_config().get("default_pack") or "").upper()

    order: list[str] = []
    for cand in (str(preferred_pack or "").upper(), gw_pack, "SQ-10", "SQ-15", "SQ-20"):
        if cand in PACK_CATALOG and cand not in order:
            order.append(cand)

    for pack_name in order:
        meta = PACK_CATALOG[pack_name]
        fpath = MODELS_DIR / meta["file"]
        if fpath.is_file() and fpath.stat().st_size == meta["bytes"]:
            return {
                "pack": pack_name,
                "model_key": meta["key"],
                "file": fpath,
                "bytes": meta["bytes"],
                "sha": meta["sha"],
                "match_pct": meta["match_pct"],
                "tokenizer_dir": _resolve_tokenizer_dir(meta["key"]),
                "ok": True,
            }
    fallback = "SQ-10"
    meta = PACK_CATALOG[fallback]
    return {
        "pack": fallback,
        "model_key": meta["key"],
        "file": MODELS_DIR / meta["file"],
        "bytes": meta["bytes"],
        "sha": meta["sha"],
        "match_pct": meta["match_pct"],
        "tokenizer_dir": _resolve_tokenizer_dir(meta["key"]),
        "ok": False,
    }


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def _read_state() -> dict | None:
    if not STATE_FILE.is_file():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _write_state(data: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _find_alive_server() -> tuple[dict | None, psutil.Process | None]:
    st = _read_state()
    if st and st.get("pid"):
        try:
            p = psutil.Process(int(st["pid"]))
            if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                return st, p
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            pass
    return st, None


def _fetch_json(url: str, timeout: float = 1.2) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))
    except Exception:
        return None


def _scan_conflicts_sync(active_port: int) -> list[dict]:
    warnings: list[dict] = []
    if not IS_MAC and active_port != 8080 and _port_open(8080):
        warnings.append({
            "kind": "qwen_8080",
            "level": "warn",
            "title": "偵測到 Port 8080 服務正在運作（可能是另一個 Strata 模型）",
            "detail": "建議錯開使用以確保 100% 顯存獨佔、跑出最高速度。",
        })
    ollama = shutil.which("ollama")
    if ollama:
        try:
            kwargs = {"creationflags": CREATE_NO_WINDOW} if IS_WIN else {}
            r = subprocess.run([ollama, "ps"], capture_output=True, text=True, errors="ignore", timeout=2.0, **kwargs)
            lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
            if len(lines) > 1:
                loaded_names = [ln.split()[0] for ln in lines[1:]]
                warnings.append({
                    "kind": "ollama_vram",
                    "level": "warn",
                    "title": f"偵測到 Ollama 正載入模型：{', '.join(loaded_names)}",
                    "detail": "Ollama 會佔用 GPU 顯存 / 統一記憶體；若要讓 Inkling-2-Mini 全速運作，建議先卸載 Ollama 模型。",
                })
        except Exception:
            pass
    if IS_MAC:
        try:
            # Check macOS swap usage and high-memory background apps on unified memory Macs
            swap_out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=1.5).stdout
            import re as _re
            m_used = _re.search(r"used\s*=\s*([\d.]+)M", swap_out or "")
            swap_gb = float(m_used.group(1)) / 1024.0 if m_used else 0.0
            vm = psutil.virtual_memory()
            ram_total_gb = vm.total / (1024 ** 3)
            if ram_total_gb <= 26.0 and (swap_gb >= 2.5 or vm.percent >= 85.0):
                # Aggregate top memory-hungry GUI apps so the user knows exactly what to close
                app_rss: dict[str, int] = {}
                for p in psutil.process_iter(["exe", "name", "memory_info"]):
                    try:
                        mi = p.info.get("memory_info")
                        if not mi or mi.rss < 80 * 1024 * 1024:
                            continue
                        exe = p.info.get("exe") or p.info.get("name") or ""
                        if "strata-inkling" in exe or "WindowServer" in exe:
                            continue
                        if ".app/" in exe:
                            label = exe.split(".app/")[0].rsplit("/", 1)[-1]
                        else:
                            continue
                        if label in ("Antigravity", "Finder", "Dock"):
                            continue
                        app_rss[label] = app_rss.get(label, 0) + mi.rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                heavy = [
                    f"{k} ({v / (1024**3):.1f} GB)"
                    for k, v in sorted(app_rss.items(), key=lambda kv: -kv[1])[:4]
                    if v >= 500 * 1024 * 1024
                ]
                if heavy or swap_gb >= 3.5:
                    detail = f"目前 macOS Swap 已用 {swap_gb:.1f} GB。"
                    if heavy:
                        detail += f" 建議先關閉高耗記憶體背景 App：{', '.join(heavy)}，可大幅降低統一記憶體換頁延遲。"
                    warnings.append({
                        "kind": "mac_memory_pressure",
                        "level": "warn",
                        "title": "偵測到 24 GB 統一記憶體壓力偏高",
                        "detail": detail,
                    })
        except Exception:
            pass
    if _GATEWAY_REF:
        gw = _GATEWAY_REF[0]
        gw_port = int(gw.config.get("port") or 11435)
        if gw.config.get("enabled", True) and not gw.running and _port_open(gw_port):
            warnings.append({
                "kind": "gateway_port_busy",
                "level": "warn",
                "title": f"開放 Agent 閘道埠口 {gw_port} 已被其他程式佔用（如 SSH 隧道）",
                "detail": f"請至「🌐 開放 Agent 接口」將連接埠改為 {gw_port + 1}（例如 11436），或釋放 {gw_port} 後重新開啟閘道。",
            })
    return warnings


def _check_conflicts(active_port: int) -> list[dict]:
    """Non-blocking cached conflict & memory check (15s TTL; never stalls /api/status or Gateway)."""
    now = time.monotonic()
    if now - _CONFLICT_CACHE["ts"] < 15.0:
        return _CONFLICT_CACHE["data"]
    if not _CONFLICT_CACHE["refreshing"]:
        _CONFLICT_CACHE["refreshing"] = True
        def _worker() -> None:
            try:
                data = _scan_conflicts_sync(active_port)
                _CONFLICT_CACHE["data"] = data
                _CONFLICT_CACHE["ts"] = time.monotonic()
            finally:
                _CONFLICT_CACHE["refreshing"] = False
        threading.Thread(target=_worker, name="ConflictScanner", daemon=True).start()
    return _CONFLICT_CACHE["data"]


def _read_active_config_defaults() -> dict:
    hw = _detect_hardware_static()
    mdl = detect_installed_model()
    out = {
        "mode": hw["recommended_mode"],
        "context": 32768,
        "kv": hw["default_kv"],
        "chunk": hw.get("default_chunk", 512),
        "port": 8081,
    }
    for cand in (
        ROOT / f"strata-{mdl['model_key']}.json",
        ROOT / "strata-inkling-sq10.json",
    ):
        if cand.is_file():
            try:
                cfg = json.loads(cand.read_text(encoding="utf-8-sig"))
                args = cfg.get("args") or []
                if "--max-context" in args:
                    out["context"] = int(args[args.index("--max-context") + 1])
                if "--kv" in args:
                    out["kv"] = str(args[args.index("--kv") + 1])
                if "--chunk" in args:
                    out["chunk"] = int(args[args.index("--chunk") + 1])
                if "--draft" in args:
                    out["mode"] = "dspark" if str(args[args.index("--draft") + 1]) == "dspark" else "fast"
                if cfg.get("port"):
                    out["port"] = int(cfg["port"])
                break
            except Exception:
                pass
    return out


def get_status() -> dict:
    hw = _detect_hardware_static()
    st, proc = _find_alive_server()
    mdl = detect_installed_model(preferred_pack=(st or {}).get("pack"))
    defaults = _read_active_config_defaults()
    port = int((st or {}).get("port") or defaults["port"])
    mode = (st or {}).get("mode") or defaults["mode"]
    context = int((st or {}).get("context") or defaults["context"])
    kv = (st or {}).get("kv") or defaults["kv"]
    chunk = int((st or {}).get("chunk") or defaults.get("chunk") or hw.get("default_chunk", 512))

    state_str = "stopped"
    health = None
    models: list[str] = []
    uptime_s = 0.0
    if proc is not None:
        try:
            uptime_s = max(0.0, time.time() - proc.create_time())
        except Exception:
            uptime_s = 0.0
        health = _fetch_json(f"http://127.0.0.1:{port}/health", timeout=1.0)
        if health and health.get("status") == "ok" and health.get("loaded", True):
            state_str = "running"
            mdata = _fetch_json(f"http://127.0.0.1:{port}/v1/models", timeout=1.0)
            if mdata and isinstance(mdata.get("data"), list):
                models = [m.get("id", "") for m in mdata["data"] if m.get("id")]
        else:
            state_str = "starting"
    elif _port_open(port):
        health = _fetch_json(f"http://127.0.0.1:{port}/health", timeout=1.0)
        if health and health.get("service") == "strata":
            state_str = "running" if health.get("loaded", True) else "starting"
            mdata = _fetch_json(f"http://127.0.0.1:{port}/v1/models", timeout=1.0)
            if mdata and isinstance(mdata.get("data"), list):
                models = [m.get("id", "") for m in mdata["data"] if m.get("id")]

    vm = psutil.virtual_memory()
    disk = shutil.disk_usage(str(ROOT))
    cpu_pct = psutil.cpu_percent(interval=None)
    engine_ok = ENGINE_EXE.is_file()

    return {
        "state": state_str,
        "pid": proc.pid if proc else None,
        "pack": mdl["pack"],
        "mode": mode,
        "port": port,
        "context": context,
        "kv": kv,
        "chunk": chunk,
        "uptime_s": round(uptime_s, 1),
        "health": health,
        "models": models,
        "platform": {
            "os": hw["os"],
            "is_mac": hw["is_mac"],
            "is_win": hw["is_win"],
            "backend": hw["backend"],
            "recommended_mode": hw["recommended_mode"],
            "default_kv": hw["default_kv"],
            "default_chunk": hw.get("default_chunk", 512),
            "is_high_bw_mac": hw.get("is_high_bw_mac", False),
            "metal_workset_gib": hw.get("metal_workset_gib"),
        },
        "endpoints": {
            "chat_ui": f"http://127.0.0.1:{port}",
            "openai_base": f"http://127.0.0.1:{port}/v1",
            "anthropic_base": f"http://127.0.0.1:{port}",
            "anthropic_messages": f"http://127.0.0.1:{port}/v1/messages",
        },
        "hardware": {
            "gpu_name": hw["gpu_name"],
            "gpu_vram_gb": hw["gpu_vram_gb"],
            "gpu_arch": hw["gpu_arch"],
            "unified_memory": hw["unified_memory"],
            "memory_label": hw["memory_label"],
            "cpu_pct": round(cpu_pct, 1),
            "ram_used_gb": round((vm.total - vm.available) / (1024**3), 1),
            "ram_total_gb": round(vm.total / (1024**3), 1),
            "ram_pct": round(vm.percent, 1),
            "disk_free_gb": round(disk.free / (1024**3), 1),
        },
        "files": {
            "model_ok": mdl["ok"],
            "model_pack": mdl["pack"],
            "model_key": mdl["model_key"],
            "model_bytes": mdl["bytes"],
            "model_sha": mdl["sha"],
            "model_match_pct": mdl["match_pct"],
            "model_size_gb": round(mdl["bytes"] / 1e9, 2),
            "installed_packs": list_installed_packs(),
            "engine_ok": engine_ok,
            "engine_version": f"strata-inkling 0.1.66 ({hw['backend'].upper()})",
        },
        "gateway": _GATEWAY_REF[0].status() if _GATEWAY_REF else None,
        "warnings": _check_conflicts(port),
    }


def start_model(params: dict) -> dict:
    with _LOCK:
        _, existing = _find_alive_server()
        if existing is not None:
            return {"ok": True, "already_running": True, "status": get_status()}

        hw = _detect_hardware_static()
        req_pack = str(params.get("pack") or "").upper().strip() or None
        mdl = detect_installed_model(preferred_pack=req_pack)

        mode = str(params.get("mode") or hw["recommended_mode"]).lower()
        if mode not in ("fast", "dspark"):
            mode = hw["recommended_mode"]
        try:
            context = int(params.get("context") or 32768)
        except (TypeError, ValueError):
            context = 32768
        if context not in (8192, 16384, 32768, 65536, 131072):
            context = 32768
        kv = str(params.get("kv") or hw["default_kv"]).lower()
        if kv not in ("k8v8", "k8v4", "fp8", "fp16"):
            kv = hw["default_kv"]
        try:
            chunk = int(params.get("chunk") or hw.get("default_chunk", 512))
        except (TypeError, ValueError):
            chunk = int(hw.get("default_chunk", 512))
        if chunk not in (256, 512, 1024):
            chunk = int(hw.get("default_chunk", 512))
        try:
            port = int(params.get("port") or 8081)
        except (TypeError, ValueError):
            port = 8081
        if not (1024 <= port <= 65535) or port == GUI_PORT:
            return {"ok": False, "error": f"連接埠 {port} 無效或與控制台（{GUI_PORT}）重複"}

        if _port_open(port):
            return {"ok": False, "error": f"連接埠 {port} 已被其他程式佔用，請先停止該程式或更換埠號。"}
        if not mdl["ok"] or (req_pack and mdl["pack"] != req_pack) or not ENGINE_EXE.is_file():
            hint = f"./install-mac.sh --pack {req_pack or mdl['pack']}" if IS_MAC else "Install.cmd"
            return {"ok": False, "error": f"找不到模型檔（{req_pack or mdl['pack']}）或引擎執行檔，請先執行 {hint}。"}

        # Persist selected pack as default for GUI & Gateway auto-wake
        if _GATEWAY_REF:
            _GATEWAY_REF[0].update_config({"default_pack": mdl["pack"]}, restart_if_running=False)
        else:
            save_gateway_config({**load_gateway_config(), "default_pack": mdl["pack"]})

        if ensure_inkling_thinking_patched is not None:
            try:
                ensure_inkling_thinking_patched(CONTROL_DIR)
            except Exception as e:
                print(f"[gui] warning: could not apply Inkling thinking patch: {e}", file=sys.stderr)

        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        draft_arg = "dspark" if mode == "dspark" else "off"
        suffix = "-dspark" if mode == "dspark" else ""
        cfg_file = ROOT / f"strata-{mdl['model_key']}{suffix}.json"
        engine_log = LOGS_DIR / f"strata-{mdl['model_key']}{suffix}.log"

        args = [
            "--native", str(mdl["file"]),
            "--max-context", str(context),
            "--kv", kv,
            "--draft", draft_arg,
        ]
        if IS_MAC or chunk != 512:
            args += ["--chunk", str(chunk)]
        vis_file = MODELS_DIR / "Inkling-2-Mini-Vision.strata"
        if vis_file.is_file() and (IS_MAC or params.get("vision")):
            args += ["--vision", str(vis_file)]

        # Empirically optimal default temperature: 1.0 for pure decode / high-BW Mac; 0.2 for DSpark burst on base M5 & RDNA2
        default_temp = 1.0 if (mode != "dspark" or hw.get("is_high_bw_mac")) else 0.2
        engine_env: dict[str, str] = {}
        if IS_MAC:
            # Empirically validated on Apple M5 24 GB:
            # - SPIN=1 yields +7%..+17% higher throughput vs SPIN=0
            # - Safe math (default) beats fast math in both accuracy and throughput
            # - 600s GPU keep-alive window keeps 13.36 GiB weights pinned hot across Agent pauses (auto-pauses on memory pressure)
            engine_env = {
                "STRATA_METAL_SPIN": "1",
                "STRATA_METAL_KEEPALIVE_MS": "250",
                "STRATA_METAL_KEEPALIVE_WINDOW_S": "600",
            }
        cfg = {
            "exe": str(ENGINE_EXE),
            "args": args,
            "cwd": str(CONTROL_DIR),
            "tokenizer": str(mdl["tokenizer_dir"]),
            "model_name": mdl["model_key"],
            "aliases": [mdl["model_key"], "inkling-2-mini-sq-10", "inkling-2-mini-sq-15", "inkling-2-mini-sq-20", "inkling-2-mini", "inkling", "strata"],
            "arch": "inkling2_loop",
            "backend": hw["backend"],
            "log": str(engine_log),
            "lib_dirs": [] if IS_MAC else [str(ROOT / "engine")],
            "port": port,
            "drafting": draft_arg,
            "parallel": 1,
            "sampling": {"temperature": default_temp},
            "fit_max_tokens": True,
            "open_browser": False,
        }
        if engine_env:
            cfg["env"] = engine_env
        cfg_file.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        stdout_log = LOGS_DIR / f"server-{mode}-{stamp}.out.log"
        stderr_log = LOGS_DIR / f"server-{mode}-{stamp}.err.log"

        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"

        out_f = open(stdout_log, "w", encoding="utf-8")
        err_f = open(stderr_log, "w", encoding="utf-8")
        try:
            popen_kwargs: dict = {
                "cwd": str(CONTROL_DIR),
                "env": env,
                "stdout": out_f,
                "stderr": err_f,
            }
            if IS_WIN:
                popen_kwargs["creationflags"] = CREATE_NO_WINDOW
            else:
                popen_kwargs["start_new_session"] = True
            proc = subprocess.Popen(
                [
                    str(PYTHON_EXE),
                    "-u",
                    str(SERVER_SCRIPT),
                    "--engine", "strata",
                    "--config", str(cfg_file),
                    "--port", str(port),
                    "--offline",
                ],
                **popen_kwargs,
            )
        finally:
            out_f.close()
            err_f.close()

        # On macOS, attach caffeinate -i -w <pid> to prevent idle sleep / App Nap while model is loaded
        if IS_MAC and shutil.which("caffeinate"):
            try:
                subprocess.Popen(
                    ["caffeinate", "-i", "-w", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception:
                pass

        _write_state({
            "pid": proc.pid,
            "mode": mode,
            "port": port,
            "context": context,
            "kv": kv,
            "chunk": chunk,
            "pack": mdl["pack"],
            "config": str(cfg_file),
            "engine_log": str(engine_log),
            "stdout": str(stdout_log),
            "stderr": str(stderr_log),
            "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        if _GATEWAY_REF:
            _GATEWAY_REF[0].invalidate_cache()
        return {"ok": True, "pid": proc.pid, "status": get_status()}


def stop_model() -> dict:
    with _LOCK:
        _, proc = _find_alive_server()
        killed: list[int] = []

        if proc is not None:
            try:
                children = proc.children(recursive=True)
            except Exception:
                children = []
            for ch in children:
                try:
                    ch.kill()
                    killed.append(ch.pid)
                except Exception:
                    pass
            try:
                proc.kill()
                killed.append(proc.pid)
            except Exception:
                pass
            psutil.wait_procs([proc] + children, timeout=6)

        root_low = str(ROOT).replace("\\", "/").lower()
        for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                name = (p.info.get("name") or "").lower()
                exe = (p.info.get("exe") or "").replace("\\", "/").lower()
                cmd = " ".join(p.info.get("cmdline") or []).replace("\\", "/").lower()
                is_our_engine = name in ("strata-inkling.exe", "strata-inkling") and exe.startswith(root_low)
                is_our_srv = "serve/server.py" in cmd and root_low in cmd
                if is_our_engine or is_our_srv:
                    p.kill()
                    killed.append(p.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if STATE_FILE.is_file():
            try:
                STATE_FILE.unlink()
            except OSError:
                pass

        if _GATEWAY_REF:
            _GATEWAY_REF[0].invalidate_cache()
        return {"ok": True, "killed": sorted(set(killed)), "status": get_status()}


def read_logs(kind: str = "engine", max_lines: int = 120) -> dict:
    st = _read_state() or {}
    if kind == "server":
        out_cand = st.get("stdout")
        err_cand = st.get("stderr")
        out_p = Path(out_cand) if (out_cand and Path(out_cand).is_file()) else None
        err_p = Path(err_cand) if (err_cand and Path(err_cand).is_file()) else None
        if not out_p:
            outs = sorted(LOGS_DIR.glob("server-*.out.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            out_p = outs[0] if outs else None
        if not err_p:
            errs = sorted(LOGS_DIR.glob("server-*.err.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            err_p = errs[0] if errs else None
        parts: list[str] = []
        shown_file = str(out_p or err_p) if (out_p or err_p) else None
        if out_p and out_p.is_file():
            txt = out_p.read_text(encoding="utf-8", errors="ignore").strip()
            if txt:
                parts.append(txt)
        if err_p and err_p.is_file():
            err_txt = err_p.read_text(encoding="utf-8", errors="ignore").strip()
            if err_txt:
                parts.append("--- STDERR ---\n" + err_txt)
        if not parts:
            return {"ok": True, "file": shown_file, "content": "（目前尚無伺服器日誌紀錄，啟動模型後會自動顯示即時日誌）"}
        lines = "\n".join(parts).splitlines()
        return {"ok": True, "file": shown_file, "content": "\n".join(lines[-max_lines:])}

    cand = st.get("engine_log")
    if cand and Path(cand).is_file():
        path: Path | None = Path(cand)
    else:
        files = sorted(LOGS_DIR.glob("strata-inkling-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        path = files[0] if files else None

    if not path or not path.is_file():
        return {"ok": True, "file": None, "content": "（目前尚無引擎日誌紀錄，啟動模型後會自動顯示即時日誌）"}
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return {"ok": True, "file": str(path), "content": "\n".join(lines[-max_lines:])}
    except Exception as e:
        return {"ok": False, "file": str(path), "content": f"讀取日誌失敗：{e}"}


class GuiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_body_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        p = parsed.path
        if p in ("/", "/index.html"):
            return self._send_file(GUI_DIR / "index.html", "text/html; charset=utf-8")
        if p == "/inkling.png":
            return self._send_file(GUI_DIR / "inkling.png", "image/png")
        if p == "/favicon.ico":
            return self._send_file(GUI_DIR / "inkling.ico", "image/x-icon")
        if p == "/api/ping":
            return self._send_json({"ok": True, "app": "strata-inkling-gui", "pid": os.getpid()})
        if p == "/api/status":
            return self._send_json(get_status())
        if p == "/api/gateway/status":
            return self._send_json({"ok": True, "gateway": _get_gateway().status()})
        if p == "/api/gateway/requests":
            return self._send_json({"ok": True, "requests": _get_gateway().recent_requests()})
        if p == "/api/logs":
            qs = urllib.parse.parse_qs(parsed.query)
            kind = (qs.get("kind") or ["engine"])[0]
            lines = int((qs.get("lines") or ["120"])[0])
            return self._send_json(read_logs(kind, min(max(lines, 20), 500)))
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        p = parsed.path
        if p == "/api/start":
            return self._send_json(start_model(self._read_body_json()))
        if p == "/api/stop":
            return self._send_json(stop_model())
        if p == "/api/gateway/start":
            body = self._read_body_json()
            gw = _get_gateway()
            body["enabled"] = True
            return self._send_json(gw.start(body))
        if p == "/api/gateway/stop":
            gw = _get_gateway()
            gw.update_config({"enabled": False}, restart_if_running=False)
            return self._send_json(gw.stop())
        if p == "/api/gateway/config":
            return self._send_json(_get_gateway().update_config(self._read_body_json(), restart_if_running=True))
        if p == "/api/open_chat":
            st = get_status()
            url = st["endpoints"]["chat_ui"]
            if IS_MAC:
                subprocess.Popen(["open", "-a", "Safari", url], start_new_session=True)
            else:
                webbrowser.open(url)
            return self._send_json({"ok": True, "url": url})
        if p == "/api/open_folder":
            body = self._read_body_json()
            target = LOGS_DIR if body.get("target") == "logs" else ROOT
            target.mkdir(parents=True, exist_ok=True)
            if IS_WIN:
                os.startfile(str(target))
            elif IS_MAC:
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
            return self._send_json({"ok": True, "path": str(target)})
        if p == "/api/shutdown_gui":
            body = self._read_body_json()
            if body.get("stop_model"):
                stop_model()
            self._send_json({"ok": True})
            threading.Thread(target=self._delayed_shutdown, daemon=True).start()
            return
        if p == "/api/chat_stream":
            return self._handle_chat_stream()
        self.send_error(HTTPStatus.NOT_FOUND)

    def _delayed_shutdown(self) -> None:
        time.sleep(0.25)
        if _HTTPD_REF:
            _HTTPD_REF[0].shutdown()

    def _handle_chat_stream(self) -> None:
        body = self._read_body_json()
        st = get_status()
        if st["state"] != "running":
            return self._send_json({"ok": False, "error": "模型尚未啟動完成，請先點擊「啟動模型服務」。"}, status=503)

        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            return self._send_json({"ok": False, "error": "請輸入測試問題。"}, status=400)

        max_tokens = min(max(int(body.get("max_tokens") or 512), 16), 4096)
        temperature = float(body.get("temperature") if body.get("temperature") is not None else 1.0)
        effort = str(body.get("reasoning_effort") or "none").strip().lower()
        if effort not in ("none", "minimal", "low", "medium", "high", "xhigh", "max"):
            effort = "none"
        port = st["port"]
        model_id = st["files"]["model_key"]

        payload = json.dumps({
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "reasoning_effort": effort,
            "stream": True,
            "stream_options": {"include_usage": True},
        }).encode("utf-8")

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        t0 = time.perf_counter()
        t_first = None
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        done_ev = {"done": True, "elapsed_s": round(time.perf_counter() - t0, 2)}
                        self.wfile.write(f"data: {json.dumps(done_ev)}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        break
                    ev = json.loads(data_str)
                    out: dict = {}
                    ch = ev.get("choices") or []
                    if ch:
                        delta = ch[0].get("delta") or {}
                        reasoning_piece = delta.get("reasoning_content") or ""
                        content_piece = delta.get("content") or ""
                        if reasoning_piece or content_piece:
                            if t_first is None:
                                t_first = time.perf_counter()
                                out["ttft_s"] = round(t_first - t0, 2)
                            if reasoning_piece:
                                out["reasoning_delta"] = reasoning_piece
                            if content_piece:
                                out["delta"] = content_piece
                    if ev.get("usage"):
                        out["usage"] = ev["usage"]
                    if ev.get("timings"):
                        out["timings"] = ev["timings"]
                    if out:
                        self.wfile.write(f"data: {json.dumps(out, ensure_ascii=False)}\n\n".encode("utf-8"))
                        self.wfile.flush()
        except Exception as e:
            err_ev = {"error": str(e)}
            try:
                self.wfile.write(f"data: {json.dumps(err_ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass


def open_app_window(url: str) -> None:
    profile_dir = GUI_DIR / ".app-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    if IS_WIN:
        candidates = [
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / r"Microsoft\Edge\Application\msedge.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / r"Microsoft\Edge\Application\msedge.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / r"Google\Chrome\Application\chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / r"Google\Chrome\Application\chrome.exe",
        ]
        for browser in candidates:
            if browser.is_file():
                subprocess.Popen(
                    [
                        str(browser),
                        f"--app={url}",
                        f"--user-data-dir={profile_dir}",
                        "--window-size=1260,860",
                        "--no-first-run",
                        "--no-default-browser-check",
                    ],
                    creationflags=CREATE_NO_WINDOW,
                )
                return
    elif IS_MAC:
        subprocess.Popen(["open", "-a", "Safari", url], start_new_session=True)
        return
    webbrowser.open(url)


def run_server() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    psutil.cpu_percent(interval=None)
    gw = _get_gateway()
    if gw.config.get("enabled", True):
        gw.start()
    httpd = _FastThreadingHTTPServer((GUI_HOST, GUI_PORT), GuiHandler)
    _HTTPD_REF.append(httpd)
    GUI_STATE_FILE.write_text(json.dumps({"pid": os.getpid(), "port": GUI_PORT}), encoding="utf-8")
    try:
        httpd.serve_forever()
    finally:
        gw.stop()
        httpd.server_close()
        if GUI_STATE_FILE.is_file():
            try:
                GUI_STATE_FILE.unlink()
            except OSError:
                pass


def launch_gui() -> None:
    url = f"http://{GUI_HOST}:{GUI_PORT}"
    if not _fetch_json(f"{url}/api/ping", timeout=0.6):
        exe = str(PYTHONW_EXE if PYTHONW_EXE.is_file() else PYTHON_EXE)
        popen_kwargs: dict = {"cwd": str(ROOT)}
        if IS_WIN:
            popen_kwargs["creationflags"] = CREATE_NO_WINDOW
        else:
            popen_kwargs["start_new_session"] = True
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL
        subprocess.Popen([exe, str(Path(__file__).resolve()), "--serve"], **popen_kwargs)
        deadline = time.time() + 12.0
        while time.time() < deadline:
            if _fetch_json(f"{url}/api/ping", timeout=0.5):
                break
            time.sleep(0.2)
    open_app_window(url)


if __name__ == "__main__":
    if "--serve" in sys.argv:
        run_server()
    else:
        launch_gui()
