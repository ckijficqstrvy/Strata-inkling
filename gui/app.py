from __future__ import annotations

import datetime
import json
import os
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
from gateway.gateway import AgentGatewayManager  # noqa: E402
CONTROL_DIR = ROOT / "control"
PYTHON_EXE = CONTROL_DIR / "python" / "python.exe"
PYTHONW_EXE = CONTROL_DIR / "python" / "pythonw.exe"
SERVER_SCRIPT = CONTROL_DIR / "serve" / "server.py"
ENGINE_EXE = ROOT / "engine" / "strata-inkling.exe"
MODEL_FILE = ROOT / "models" / "Inkling-2-Mini-Strata" / "Inkling-2-Mini-SQ-10.strata"
TOKENIZER_DIR = ROOT / "packs" / "inkling-2-mini-sq-10" / "tokenizer"
LOGS_DIR = ROOT / "logs"
STATE_FILE = LOGS_DIR / "server-process.json"
GUI_STATE_FILE = LOGS_DIR / "gui-process.json"

GUI_HOST = "127.0.0.1"
GUI_PORT = 8088
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_LOCK = threading.Lock()
_CONFLICT_CACHE = {"ts": 0.0, "data": []}
_HTTPD_REF: list[ThreadingHTTPServer] = []
_GATEWAY_REF: list[AgentGatewayManager] = []


def _get_gateway() -> AgentGatewayManager:
    if not _GATEWAY_REF:
        _GATEWAY_REF.append(AgentGatewayManager(get_status, start_model, stop_model))
    return _GATEWAY_REF[0]


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


def _check_conflicts(active_port: int) -> list[dict]:
    now = time.time()
    if now - _CONFLICT_CACHE["ts"] < 5.0:
        return _CONFLICT_CACHE["data"]
    warnings: list[dict] = []
    # 1. Check if G:\AI\Strata (Qwen) on 8080 is running
    if active_port != 8080 and _port_open(8080):
        warnings.append({
            "kind": "qwen_8080",
            "level": "warn",
            "title": "偵測到 Port 8080 服務正在運作（可能是 G:\\AI\\Strata 的 Qwen）",
            "detail": "RX 6700 XT 只有 12 GB 顯存，建議先停止 Qwen 服務再啟動 Inkling-2-Mini，確保 100% VRAM 全速運作。",
        })
    # 2. Check if Ollama currently has any model loaded in VRAM
    ollama = shutil.which("ollama")
    if ollama:
        try:
            r = subprocess.run(
                [ollama, "ps"],
                capture_output=True,
                text=True,
                timeout=2.5,
                creationflags=CREATE_NO_WINDOW,
            )
            lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
            if len(lines) > 1:
                loaded_names = [ln.split()[0] for ln in lines[1:]]
                warnings.append({
                    "kind": "ollama_vram",
                    "level": "warn",
                    "title": f"偵測到 Ollama 正載入模型：{', '.join(loaded_names)}",
                    "detail": "Ollama 會佔用 GPU 顯存；若要讓 Inkling-2-Mini 100% 駐留顯存跑滿速，請先卸載 Ollama 模型。",
                })
        except Exception:
            pass
    _CONFLICT_CACHE["ts"] = now
    _CONFLICT_CACHE["data"] = warnings
    return warnings


def _read_active_config_defaults() -> dict:
    cfg_path = ROOT / "strata-inkling-sq10.json"
    out = {"mode": "fast", "context": 32768, "kv": "k8v8", "port": 8081}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
            args = cfg.get("args") or []
            if "--max-context" in args:
                out["context"] = int(args[args.index("--max-context") + 1])
            if "--kv" in args:
                out["kv"] = str(args[args.index("--kv") + 1])
            if cfg.get("port"):
                out["port"] = int(cfg["port"])
        except Exception:
            pass
    return out


def get_status() -> dict:
    st, proc = _find_alive_server()
    defaults = _read_active_config_defaults()
    port = int((st or {}).get("port") or defaults["port"])
    mode = (st or {}).get("mode") or defaults["mode"]
    context = int((st or {}).get("context") or defaults["context"])
    kv = (st or {}).get("kv") or defaults["kv"]

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
        # Something is already serving on port (maybe started via CMD)
        health = _fetch_json(f"http://127.0.0.1:{port}/health", timeout=1.0)
        if health and health.get("service") == "strata":
            state_str = "running" if health.get("loaded", True) else "starting"
            mdata = _fetch_json(f"http://127.0.0.1:{port}/v1/models", timeout=1.0)
            if mdata and isinstance(mdata.get("data"), list):
                models = [m.get("id", "") for m in mdata["data"] if m.get("id")]

    vm = psutil.virtual_memory()
    disk = shutil.disk_usage(str(ROOT))
    cpu_pct = psutil.cpu_percent(interval=None)
    model_ok = MODEL_FILE.is_file() and MODEL_FILE.stat().st_size == 10_471_526_400
    engine_ok = ENGINE_EXE.is_file()

    return {
        "state": state_str,
        "pid": proc.pid if proc else None,
        "mode": mode,
        "port": port,
        "context": context,
        "kv": kv,
        "uptime_s": round(uptime_s, 1),
        "health": health,
        "models": models,
        "endpoints": {
            "chat_ui": f"http://127.0.0.1:{port}",
            "openai_base": f"http://127.0.0.1:{port}/v1",
            "anthropic_base": f"http://127.0.0.1:{port}",
            "anthropic_messages": f"http://127.0.0.1:{port}/v1/messages",
        },
        "hardware": {
            "gpu_name": "AMD Radeon RX 6700 XT",
            "gpu_vram_gb": 12,
            "gpu_arch": "RDNA2 (gfx1031)",
            "cpu_pct": round(cpu_pct, 1),
            "ram_used_gb": round((vm.total - vm.available) / (1024**3), 1),
            "ram_total_gb": round(vm.total / (1024**3), 1),
            "ram_pct": round(vm.percent, 1),
            "disk_free_gb": round(disk.free / (1024**3), 1),
        },
        "files": {
            "model_ok": model_ok,
            "model_size_gb": round(MODEL_FILE.stat().st_size / 1e9, 2) if MODEL_FILE.is_file() else 0.0,
            "engine_ok": engine_ok,
            "engine_version": "strata-inkling 0.1.66 (Strata v0.2.20)",
        },
        "gateway": _GATEWAY_REF[0].status() if _GATEWAY_REF else None,
        "warnings": _check_conflicts(port),
    }


def start_model(params: dict) -> dict:
    with _LOCK:
        _, existing = _find_alive_server()
        if existing is not None:
            return {"ok": True, "already_running": True, "status": get_status()}

        mode = str(params.get("mode") or "fast").lower()
        if mode not in ("fast", "dspark"):
            mode = "fast"
        try:
            context = int(params.get("context") or 32768)
        except (TypeError, ValueError):
            context = 32768
        if context not in (8192, 16384, 32768, 65536):
            context = 32768
        kv = str(params.get("kv") or "k8v8").lower()
        if kv not in ("k8v8", "k8v4", "fp8"):
            kv = "k8v8"
        try:
            port = int(params.get("port") or 8081)
        except (TypeError, ValueError):
            port = 8081
        if not (1024 <= port <= 65535) or port == GUI_PORT:
            return {"ok": False, "error": f"連接埠 {port} 無效或與控制台（{GUI_PORT}）重複"}

        if _port_open(port):
            return {"ok": False, "error": f"連接埠 {port} 已被其他程式佔用，請先停止該程式或更換埠號。"}
        if not MODEL_FILE.is_file() or not ENGINE_EXE.is_file():
            return {"ok": False, "error": "找不到模型檔或引擎執行檔，請檢查 g:\\AI\\Strata-inkling 目錄。"}

        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        draft_arg = "dspark" if mode == "dspark" else "off"
        cfg_file = ROOT / ("strata-inkling-sq10-dspark.json" if mode == "dspark" else "strata-inkling-sq10.json")
        engine_log = LOGS_DIR / ("strata-inkling-sq10-dspark.log" if mode == "dspark" else "strata-inkling-sq10.log")

        cfg = {
            "exe": str(ENGINE_EXE),
            "args": [
                "--native", str(MODEL_FILE),
                "--max-context", str(context),
                "--kv", kv,
                "--draft", draft_arg,
            ],
            "cwd": str(CONTROL_DIR),
            "tokenizer": str(TOKENIZER_DIR),
            "model_name": "inkling-2-mini-sq-10",
            "aliases": ["inkling-2-mini", "inkling", "strata"],
            "arch": "inkling2_loop",
            "backend": "hip",
            "log": str(engine_log),
            "lib_dirs": [str(ROOT / "engine")],
            "port": port,
            "drafting": draft_arg,
            "parallel": 1,
            "sampling": {"temperature": 0.2 if mode == "dspark" else 1.0},
            "fit_max_tokens": True,
            "open_browser": False,
        }
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
                cwd=str(CONTROL_DIR),
                env=env,
                stdout=out_f,
                stderr=err_f,
                creationflags=CREATE_NO_WINDOW,
            )
        finally:
            out_f.close()
            err_f.close()

        _write_state({
            "pid": proc.pid,
            "mode": mode,
            "port": port,
            "context": context,
            "kv": kv,
            "config": str(cfg_file),
            "engine_log": str(engine_log),
            "stdout": str(stdout_log),
            "stderr": str(stderr_log),
            "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        return {"ok": True, "pid": proc.pid, "status": get_status()}


def stop_model() -> dict:
    with _LOCK:
        st, proc = _find_alive_server()
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

        # Clean up any orphaned strata-inkling.exe or server.py owned by this workspace
        root_low = str(ROOT).lower()
        for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                name = (p.info.get("name") or "").lower()
                exe = (p.info.get("exe") or "").lower()
                cmd = " ".join(p.info.get("cmdline") or []).lower()
                is_our_engine = name == "strata-inkling.exe" and exe.startswith(root_low)
                is_our_srv = "serve\\server.py" in cmd and root_low in cmd
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
            body = self._read_body_json()
            return self._send_json(start_model(body))
        if p == "/api/stop":
            return self._send_json(stop_model())
        if p == "/api/gateway/start":
            body = self._read_body_json()
            gw = _get_gateway()
            if body:
                body["enabled"] = True
            return self._send_json(gw.start(body or {"enabled": True}))
        if p == "/api/gateway/stop":
            gw = _get_gateway()
            gw.update_config({"enabled": False}, restart_if_running=False)
            return self._send_json(gw.stop())
        if p == "/api/gateway/config":
            body = self._read_body_json()
            gw = _get_gateway()
            return self._send_json(gw.update_config(body, restart_if_running=True))
        if p == "/api/open_chat":
            st = get_status()
            url = st["endpoints"]["chat_ui"]
            webbrowser.open(url)
            return self._send_json({"ok": True, "url": url})
        if p == "/api/open_folder":
            body = self._read_body_json()
            target = LOGS_DIR if body.get("target") == "logs" else ROOT
            target.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(target))
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

        max_tokens = min(max(int(body.get("max_tokens") or 256), 16), 2048)
        temperature = float(body.get("temperature") if body.get("temperature") is not None else 1.0)
        port = st["port"]

        payload = json.dumps({
            "model": "inkling-2-mini-sq-10",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
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
                        piece = delta.get("content") or delta.get("reasoning_content") or ""
                        if piece:
                            if t_first is None:
                                t_first = time.perf_counter()
                                out["ttft_s"] = round(t_first - t0, 2)
                            out["delta"] = piece
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
    webbrowser.open(url)


def run_server() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    psutil.cpu_percent(interval=None)
    gw = _get_gateway()
    if gw.config.get("enabled", True):
        gw.start()
    httpd = ThreadingHTTPServer((GUI_HOST, GUI_PORT), GuiHandler)
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
        subprocess.Popen(
            [exe, str(Path(__file__).resolve()), "--serve"],
            cwd=str(ROOT),
            creationflags=CREATE_NO_WINDOW,
        )
        deadline = time.time() + 12.0
        while time.time() < deadline:
            if _fetch_json(f"{url}/api/ping", timeout=0.5):
                break
            time.sleep(0.2)
    open_app_window(url)


if __name__ == "__main__":
    if "--serve" in sys.argv:
        run_server()
    elif "--launch" in sys.argv:
        launch_gui()
    else:
        launch_gui()
