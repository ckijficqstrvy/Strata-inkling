from __future__ import annotations

import collections
import datetime
import http.client
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
GATEWAY_CONFIG_FILE = ROOT / "gateway-config.json"


def _mac_default_mode() -> str:
    """Base Apple Silicon (M1-M5 <=24GB) runs 2.5-2.8x faster on CJK/long-ctx with draft=off;
    Max/Ultra high-bandwidth chips default to dspark."""
    if sys.platform != "darwin":
        return "fast"
    try:
        chip = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True, timeout=2
        ).strip()
        if any(tier in chip for tier in ("Max", "Ultra")):
            return "dspark"
    except Exception:
        pass
    return "fast"


DEFAULT_GATEWAY_CONFIG = {
    "enabled": True,
    "host": "127.0.0.1",      # "127.0.0.1" (local only) or "0.0.0.0" (LAN / Tailscale)
    "port": 11435,            # Clean default (avoids Ollama 11434 & LM Studio 1234)
    "api_key": "",            # Optional; recommended when host == "0.0.0.0"
    "auto_wake": True,        # Auto-start Strata engine when an external Agent calls
    "idle_unload_min": 0,     # 0 = never auto-unload; >0 = unload model after N idle minutes
    "default_mode": _mac_default_mode(),
    "default_context": 32768,
    "default_kv": "fp8" if sys.platform == "darwin" else "k8v8",
    "default_chunk": 1024 if sys.platform == "darwin" else 512,
    "backend_port": 8081,
}

MODEL_PRIMARY = "inkling-2-mini-sq-15" if sys.platform == "darwin" else "inkling-2-mini-sq-10"
MODEL_ALIASES = [
    "inkling-2-mini-sq-10",
    "inkling-2-mini-sq-15",
    "inkling-2-mini-sq-20",
    "inkling-2-mini",
    "inkling-2-mini:latest",
    "inkling",
    "strata",
]
MODEL_BYTES = 14_348_316_672 if sys.platform == "darwin" else 10_471_526_400
MODEL_DIGEST = (
    "2ea32eed533afba257e2cbee7b073dc2c3da80fe55d1d2d794779b5498e30737"
    if sys.platform == "darwin"
    else "ac4d452ad03e4930abd9d46f9c5eec22b2f75a1da45a1e49bd24ccd58b065934"
)


def load_gateway_config() -> dict:
    cfg = dict(DEFAULT_GATEWAY_CONFIG)
    if GATEWAY_CONFIG_FILE.is_file():
        try:
            raw = json.loads(GATEWAY_CONFIG_FILE.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                cfg.update(raw)
        except Exception:
            pass
    return cfg


def save_gateway_config(cfg: dict) -> dict:
    merged = dict(DEFAULT_GATEWAY_CONFIG)
    merged.update(cfg)
    GATEWAY_CONFIG_FILE.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    return merged


_LAN_IP_CACHE: dict = {"ts": 0.0, "ips": []}


def get_lan_ips() -> list[str]:
    now = time.monotonic()
    if now - _LAN_IP_CACHE["ts"] < 30.0:
        return list(_LAN_IP_CACHE["ips"])
    ips: list[str] = []
    try:
        import psutil
        for _, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET:
                    ip = a.address
                    if not ip.startswith(("127.", "169.254.", "172.17.", "172.31.")):
                        if ip not in ips:
                            ips.append(ip)
    except Exception:
        pass
    _LAN_IP_CACHE["ts"] = now
    _LAN_IP_CACHE["ips"] = ips
    return list(ips)


class _FastThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64


class _BackendConnPool:
    """Thread-safe HTTP/1.1 Keep-Alive connection pool to loopback Strata backend with TCP_NODELAY."""

    def __init__(self, maxsize: int = 16) -> None:
        self._lock = threading.Lock()
        self._pools: dict[int, queue.LifoQueue[http.client.HTTPConnection]] = {}
        self._maxsize = maxsize

    def _pool_for(self, port: int) -> queue.LifoQueue[http.client.HTTPConnection]:
        with self._lock:
            q = self._pools.get(port)
            if q is None:
                q = queue.LifoQueue(maxsize=self._maxsize)
                self._pools[port] = q
            return q

    def acquire(self, port: int, timeout: float = 300.0) -> http.client.HTTPConnection:
        q = self._pool_for(port)
        while True:
            try:
                conn = q.get_nowait()
            except queue.Empty:
                break
            if conn.sock is not None:
                conn.timeout = timeout
                try:
                    conn.sock.settimeout(timeout)
                except OSError:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    continue
                return conn
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        conn.connect()
        if conn.sock is not None:
            try:
                conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        return conn

    def release(self, port: int, conn: http.client.HTTPConnection) -> None:
        if conn.sock is None:
            return
        q = self._pool_for(port)
        try:
            q.put_nowait(conn)
        except queue.Full:
            try:
                conn.close()
            except Exception:
                pass

    def discard(self, conn: http.client.HTTPConnection | None) -> None:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def request(
        self,
        port: int,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 300.0,
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        hdrs = dict(headers or {})
        hdrs.setdefault("Connection", "keep-alive")
        if body is not None:
            hdrs.setdefault("Content-Length", str(len(body)))
        last_err: Exception | None = None
        for attempt in range(2):
            conn = self.acquire(port, timeout=timeout)
            try:
                conn.request(method, path, body=body, headers=hdrs)
                resp = conn.getresponse()
                return conn, resp
            except (http.client.HTTPException, OSError) as e:
                self.discard(conn)
                last_err = e
                if attempt == 0:
                    continue
                raise
        raise last_err or RuntimeError("backend connection failed")

    def close_all(self) -> None:
        with self._lock:
            pools = list(self._pools.values())
            self._pools.clear()
        for q in pools:
            while True:
                try:
                    c = q.get_nowait()
                    self.discard(c)
                except queue.Empty:
                    break


class AgentGatewayManager:
    """Unified OpenAI + Ollama + LM Studio + Anthropic gateway with auto-wake & idle unload."""

    def __init__(
        self,
        get_model_status: Callable[[], dict],
        start_model_fn: Callable[[dict], dict],
        stop_model_fn: Callable[[], dict],
    ) -> None:
        self.get_model_status = get_model_status
        self.start_model_fn = start_model_fn
        self.stop_model_fn = stop_model_fn
        self.config = load_gateway_config()
        self._lock = threading.Lock()
        self._wake_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._idle_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started_at = 0.0
        self._last_activity = time.time()
        self._active_requests = 0
        self._total_requests = 0
        self._history: collections.deque[dict] = collections.deque(maxlen=60)
        self._pool = _BackendConnPool(maxsize=16)
        # Fast-path TTL caches to eliminate psutil / subprocess overhead on hot API paths
        self._ready_cache: dict = {"ts": 0.0, "ready": False, "port": int(self.config.get("backend_port") or 8081)}
        self._status_cache: dict = {"ts": 0.0, "data": None}

    @property
    def running(self) -> bool:
        return self._httpd is not None and self._thread is not None and self._thread.is_alive()

    def start(self, override_cfg: dict | None = None) -> dict:
        with self._lock:
            if override_cfg:
                self.config = save_gateway_config({**self.config, **override_cfg})
            if self.running:
                return {"ok": True, "already_running": True, "gateway": self.status()}

            host = str(self.config.get("host") or "127.0.0.1").strip()
            if host not in ("127.0.0.1", "0.0.0.0"):
                host = "127.0.0.1"
            port = int(self.config.get("port") or 11435)
            if not (1024 <= port <= 65535):
                return {"ok": False, "error": f"無效的閘道連接埠：{port}"}

            manager = self

            class Handler(_GatewayHTTPHandler):
                mgr = manager

            try:
                httpd = _FastThreadingHTTPServer((host, port), Handler)
            except OSError as e:
                return {"ok": False, "error": f"無法綁定 {host}:{port}（可能已被佔用）：{e}"}

            self._httpd = httpd
            self._stop_event.clear()
            self._started_at = time.time()
            self._last_activity = time.time()
            self._thread = threading.Thread(target=httpd.serve_forever, name="AgentGatewayHTTP", daemon=True)
            self._thread.start()

            if self._idle_thread is None or not self._idle_thread.is_alive():
                self._idle_thread = threading.Thread(target=self._idle_watcher, name="AgentGatewayIdle", daemon=True)
                self._idle_thread.start()

            return {"ok": True, "gateway": self.status()}

    def stop(self) -> dict:
        with self._lock:
            self._stop_event.set()
            if self._httpd is not None:
                try:
                    self._httpd.shutdown()
                    self._httpd.server_close()
                except Exception:
                    pass
                self._httpd = None
            self._thread = None
            self._pool.close_all()
            return {"ok": True, "gateway": self.status()}

    def update_config(self, new_cfg: dict, restart_if_running: bool = True) -> dict:
        was_running = self.running
        prev_host = self.config.get("host")
        prev_port = self.config.get("port")
        self.config = save_gateway_config({**self.config, **new_cfg})
        need_restart = was_running and restart_if_running and (
            self.config.get("host") != prev_host or self.config.get("port") != prev_port
        )
        if need_restart:
            self.stop()
            return self.start()
        return {"ok": True, "gateway": self.status()}

    def status(self) -> dict:
        host = str(self.config.get("host") or "127.0.0.1")
        port = int(self.config.get("port") or 11435)
        lan_ips = get_lan_ips()
        lan_urls = [f"http://{ip}:{port}" for ip in lan_ips] if host == "0.0.0.0" else []
        idle_s = round(max(0.0, time.time() - self._last_activity), 1) if self.running else 0.0
        return {
            "running": self.running,
            "host": host,
            "port": port,
            "lan_enabled": host == "0.0.0.0",
            "api_key_set": bool(self.config.get("api_key")),
            "api_key": str(self.config.get("api_key") or ""),
            "auto_wake": bool(self.config.get("auto_wake", True)),
            "idle_unload_min": int(self.config.get("idle_unload_min") or 0),
            "uptime_s": round(max(0.0, time.time() - self._started_at), 1) if self.running else 0.0,
            "idle_s": idle_s,
            "active_requests": self._active_requests,
            "total_requests": self._total_requests,
            "local_base": f"http://127.0.0.1:{port}",
            "lan_bases": lan_urls,
            "endpoints": {
                "openai": f"http://127.0.0.1:{port}/v1",
                "ollama": f"http://127.0.0.1:{port}",
                "lmstudio": f"http://127.0.0.1:{port}/v1",
                "anthropic": f"http://127.0.0.1:{port}",
            },
        }

    def recent_requests(self) -> list[dict]:
        return list(self._history)

    def record_request(self, entry: dict) -> None:
        with self._stats_lock:
            self._last_activity = time.time()
            self._total_requests += 1
            self._history.appendleft(entry)

    def _inc_active(self, delta: int) -> None:
        with self._stats_lock:
            self._active_requests = max(0, self._active_requests + delta)

    def invalidate_cache(self) -> None:
        self._ready_cache["ts"] = 0.0
        self._status_cache["ts"] = 0.0

    def cached_model_status(self, max_age_s: float = 3.0) -> dict:
        """Return model status with short TTL cache so /health and /v1/models never stall on psutil/ollama."""
        now = time.monotonic()
        cached = self._status_cache.get("data")
        if cached is not None and (now - self._status_cache["ts"]) < max_age_s:
            return cached
        st = self.get_model_status()
        self._status_cache["data"] = st
        self._status_cache["ts"] = now
        if st.get("state") == "running":
            self._ready_cache["ts"] = now
            self._ready_cache["ready"] = True
            self._ready_cache["port"] = int(st.get("port") or self.config.get("backend_port") or 8081)
        return st

    def _probe_backend_fast(self, port: int) -> bool:
        conn = None
        try:
            conn, resp = self._pool.request(port, "GET", "/health", timeout=0.6)
            data = resp.read()
            if resp.status == 200 and not resp.will_close:
                self._pool.release(port, conn)
            else:
                self._pool.discard(conn)
            if resp.status == 200:
                obj = json.loads(data.decode("utf-8", errors="ignore"))
                return obj.get("status") == "ok" and obj.get("loaded", True)
        except Exception:
            self._pool.discard(conn)
        return False

    def ensure_backend_ready(self) -> tuple[bool, int, bool, str]:
        """Returns (ready, backend_port, auto_woke, error_msg). Uses 2s fast-path cache to stay sub-ms."""
        now = time.monotonic()
        if self._ready_cache["ready"] and (now - self._ready_cache["ts"]) < 2.0:
            return True, int(self._ready_cache["port"]), False, ""

        fast_port = int(self._ready_cache.get("port") or self.config.get("backend_port") or 8081)
        if self._probe_backend_fast(fast_port):
            self._ready_cache["ts"] = now
            self._ready_cache["ready"] = True
            self._ready_cache["port"] = fast_port
            return True, fast_port, False, ""

        st = self.get_model_status()
        self._status_cache["data"] = st
        self._status_cache["ts"] = now
        bport = int(st.get("port") or self.config.get("backend_port") or 8081)
        if st.get("state") == "running":
            self._ready_cache.update({"ts": now, "ready": True, "port": bport})
            return True, bport, False, ""

        if st.get("state") == "stopped":
            if not self.config.get("auto_wake", True):
                return False, bport, False, "Inkling-2-Mini 模型尚未啟動，且已關閉自動喚醒功能。請先於控制台啟動模型。"

        with self._wake_lock:
            st = self.get_model_status()
            bport = int(st.get("port") or self.config.get("backend_port") or 8081)
            if st.get("state") == "running":
                self._ready_cache.update({"ts": time.monotonic(), "ready": True, "port": bport})
                return True, bport, False, ""
            auto_woke = False
            if st.get("state") == "stopped":
                res = self.start_model_fn({
                    "mode": self.config.get("default_mode", DEFAULT_GATEWAY_CONFIG["default_mode"]),
                    "context": int(self.config.get("default_context") or 32768),
                    "kv": self.config.get("default_kv", DEFAULT_GATEWAY_CONFIG["default_kv"]),
                    "chunk": int(self.config.get("default_chunk") or DEFAULT_GATEWAY_CONFIG["default_chunk"]),
                    "port": bport,
                })
                if not res.get("ok"):
                    return False, bport, False, str(res.get("error") or "自動喚醒模型失敗")
                auto_woke = True

            deadline = time.time() + 120.0
            while time.time() < deadline:
                if self._probe_backend_fast(bport):
                    self.invalidate_cache()
                    self._ready_cache.update({"ts": time.monotonic(), "ready": True, "port": bport})
                    return True, bport, auto_woke, ""
                st = self.get_model_status()
                if st.get("state") == "running":
                    rport = int(st.get("port") or bport)
                    self._ready_cache.update({"ts": time.monotonic(), "ready": True, "port": rport})
                    return True, rport, auto_woke, ""
                if st.get("state") == "stopped":
                    return False, bport, auto_woke, "模型啟動過程中意外停止，請查看引擎日誌。"
                time.sleep(0.25)
            return False, bport, auto_woke, "等待模型載入逾時（120 秒）。"

    def _idle_watcher(self) -> None:
        while not self._stop_event.wait(15.0):
            try:
                limit_min = int(self.config.get("idle_unload_min") or 0)
                if limit_min <= 0 or self._active_requests > 0:
                    continue
                if time.time() - self._last_activity < limit_min * 60:
                    continue
                st = self.get_model_status()
                if st.get("state") == "running":
                    self.stop_model_fn()
                    self.record_request({
                        "time": datetime.datetime.now().strftime("%H:%M:%S"),
                        "protocol": "System",
                        "method": "IDLE",
                        "path": f"自動釋放顯存（閒置 {limit_min} 分鐘）",
                        "client": "localhost",
                        "model": MODEL_PRIMARY,
                        "status": 200,
                        "prompt_tok": 0,
                        "gen_tok": 0,
                        "tps": 0.0,
                        "elapsed_s": 0.0,
                        "auto_woke": False,
                    })
            except Exception:
                pass


class _GatewayHTTPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mgr: AgentGatewayManager

    def setup(self) -> None:
        super().setup()
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def log_message(self, format: str, *args) -> None:
        return

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, x-api-key, api-key, anthropic-version, anthropic-beta")
        self.send_header("Access-Control-Max-Age", "86400")

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or "0")
        return self.rfile.read(length) if length > 0 else b""

    def _check_auth(self) -> bool:
        expected = str(self.mgr.config.get("api_key") or "").strip()
        if not expected:
            return True
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer ") and auth[7:].strip() == expected:
            return True
        if (self.headers.get("x-api-key") or "").strip() == expected:
            return True
        if (self.headers.get("api-key") or "").strip() == expected:
            return True
        self._send_json({"error": {"message": "Invalid or missing API key", "type": "authentication_error"}}, status=401)
        return False

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:
        self.send_response(HTTPStatus.OK)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        # Public probe endpoints (Ollama & health probes expect 200 without auth)
        if path == "/":
            return self._send_text("Ollama is running")
        if path in ("/health", "/v1/health"):
            st = self.mgr.cached_model_status(max_age_s=2.0)
            info = self._active_model_info(st)
            return self._send_json({
                "status": "ok",
                "gateway": "strata-inkling-open-gateway",
                "model_state": st.get("state"),
                "model": info["key"],
                "max_context": st.get("context", 32768),
                "auto_wake": bool(self.mgr.config.get("auto_wake", True)),
            })
        if path == "/api/version":
            return self._send_json({"version": "0.6.8-strata-inkling"})

        if not self._check_auth():
            return

        if path in ("/v1/models", "/models"):
            return self._handle_openai_models()
        if path.startswith("/v1/models/"):
            mid = urllib.parse.unquote(path[len("/v1/models/"):])
            return self._send_json(self._openai_model_obj(mid))
        if path == "/api/v0/models":
            return self._handle_lmstudio_models()
        if path == "/api/tags":
            return self._handle_ollama_tags()
        if path == "/api/ps":
            return self._handle_ollama_ps()

        self._send_json({"error": {"message": f"Unknown GET endpoint: {path}"}}, status=404)

    def do_POST(self) -> None:
        if not self._check_auth():
            return
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"

        if path == "/api/show":
            return self._handle_ollama_show()
        if path == "/api/chat":
            return self._handle_ollama_chat()
        if path == "/api/generate":
            return self._handle_ollama_generate()
        if path == "/v1/chat/completions":
            return self._proxy_inference("OpenAI", "/v1/chat/completions")
        if path == "/v1/completions":
            return self._handle_legacy_completions()
        if path == "/v1/responses":
            return self._proxy_inference("OpenAI-Responses", "/v1/responses")
        if path == "/v1/messages":
            return self._proxy_inference("Anthropic", "/v1/messages")

        self._send_json({"error": {"message": f"Unknown POST endpoint: {path}"}}, status=404)

    def _active_model_info(self, st: dict | None = None) -> dict:
        if st is None:
            st = self.mgr.cached_model_status(max_age_s=3.0)
        files = st.get("files") or {}
        pack = files.get("model_pack") or ("SQ-15" if sys.platform == "darwin" else "SQ-10")
        key = files.get("model_key") or MODEL_PRIMARY
        nbytes = int(files.get("model_bytes") or MODEL_BYTES)
        sha = str(files.get("model_sha") or MODEL_DIGEST)
        ctx = int(st.get("context") or 32768)
        aliases = [key, f"{key}:latest", "inkling-2-mini", "inkling-2-mini:latest", "inkling", "strata"]
        dedup = []
        for a in aliases:
            if a not in dedup:
                dedup.append(a)
        return {"pack": pack, "key": key, "bytes": nbytes, "sha": sha, "ctx": ctx, "aliases": dedup, "state": st.get("state")}

    # ---------- Model Catalogs ----------
    def _openai_model_obj(self, model_id: str) -> dict:
        info = self._active_model_info()
        return {
            "id": model_id,
            "object": "model",
            "created": 1763900000,
            "owned_by": "strata-inkling",
            "max_model_len": info["ctx"],
            "context_length": info["ctx"],
        }

    def _handle_openai_models(self) -> None:
        info = self._active_model_info()
        data = [self._openai_model_obj(m) for m in info["aliases"]]
        self._send_json({"object": "list", "data": data})

    def _handle_lmstudio_models(self) -> None:
        info = self._active_model_info()
        state = "loaded" if info["state"] == "running" else "not-loaded"
        ctx = info["ctx"]
        self._send_json({
            "object": "list",
            "data": [
                {
                    "id": info["key"],
                    "object": "model",
                    "type": "llm",
                    "publisher": "thinkingmachines / strata",
                    "arch": "inkling2_loop",
                    "compatibility_type": "strata",
                    "quantization": info["pack"],
                    "state": state,
                    "max_context_length": ctx,
                    "loaded_context_length": ctx if state == "loaded" else 0,
                }
            ],
        })

    def _ollama_model_entry(self, name: str) -> dict:
        info = self._active_model_info()
        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "name": name,
            "model": name,
            "modified_at": now_iso,
            "size": info["bytes"],
            "digest": info["sha"],
            "details": {
                "parent_model": "",
                "format": "strata",
                "family": "inkling2",
                "families": ["inkling2"],
                "parameter_size": "26.4B",
                "quantization_level": info["pack"],
            },
        }

    def _handle_ollama_tags(self) -> None:
        info = self._active_model_info()
        self._send_json({
            "models": [
                self._ollama_model_entry("inkling-2-mini:latest"),
                self._ollama_model_entry(f"{info['key']}:latest"),
            ]
        })

    def _handle_ollama_ps(self) -> None:
        info = self._active_model_info()
        if info["state"] != "running":
            return self._send_json({"models": []})
        exp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = self._ollama_model_entry("inkling-2-mini:latest")
        entry["size_vram"] = int(info["bytes"] * 1.08)
        entry["expires_at"] = exp
        entry["context_length"] = info["ctx"]
        self._send_json({"models": [entry]})

    def _handle_ollama_show(self) -> None:
        info = self._active_model_info()
        ctx = info["ctx"]
        self._send_json({
            "modelfile": f"# Strata Inkling-2-Mini {info['pack']} (Strata native)\nFROM Inkling-2-Mini-{info['pack']}.strata",
            "parameters": f"num_ctx {ctx}\ntemperature 1.0",
            "template": "{{ .Prompt }}",
            "details": {
                "parent_model": "",
                "format": "strata",
                "family": "inkling2",
                "families": ["inkling2"],
                "parameter_size": "26.4B",
                "quantization_level": info["pack"],
            },
            "model_info": {
                "general.architecture": "inkling2",
                "general.parameter_count": 26400000000,
                "inkling2.context_length": ctx,
            },
            "capabilities": ["completion", "tools"],
        })

    # ---------- OpenAI / Anthropic / Responses Pass-Through Proxy ----------
    def _proxy_inference(self, proto_label: str, backend_path: str) -> None:
        t0 = time.perf_counter()
        raw = self._read_raw_body()
        req_model = MODEL_PRIMARY
        is_stream = False
        try:
            body_obj = json.loads(raw.decode("utf-8")) if raw else {}
            if isinstance(body_obj, dict):
                req_model = str(body_obj.get("model") or MODEL_PRIMARY)
                is_stream = bool(body_obj.get("stream"))
                # Ensure usage & timings come back on OpenAI stream when client didn't object
                if proto_label == "OpenAI" and is_stream and "stream_options" not in body_obj:
                    body_obj["stream_options"] = {"include_usage": True}
                    raw = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
        except Exception:
            body_obj = {}

        self.mgr._inc_active(1)
        auto_woke = False
        prompt_tok = 0
        gen_tok = 0
        tps = 0.0
        status_code = 500
        conn: http.client.HTTPConnection | None = None
        reusable = False
        bport = int(self.mgr.config.get("backend_port") or 8081)
        try:
            ok, bport, auto_woke, err = self.mgr.ensure_backend_ready()
            if not ok:
                status_code = 503
                self._send_json({"error": {"message": err, "type": "service_unavailable"}}, status=503)
                return

            fwd_headers = {"Content-Type": self.headers.get("Content-Type") or "application/json"}
            for h in ("anthropic-version", "anthropic-beta", "Accept"):
                if self.headers.get(h):
                    fwd_headers[h] = self.headers.get(h)

            conn, resp = self.mgr._pool.request(
                bport, "POST", backend_path, body=raw, headers=fwd_headers, timeout=300.0
            )
            status_code = resp.status
            ctype = resp.getheader("Content-Type") or "application/json"

            if status_code >= 400:
                err_bytes = resp.read()
                reusable = not resp.will_close
                self.send_response(status_code)
                self._cors_headers()
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(err_bytes)))
                self.end_headers()
                self.wfile.write(err_bytes)
                return

            if "text/event-stream" in ctype.lower() or is_stream:
                self.send_response(status_code)
                self._cors_headers()
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                wfile_write = self.wfile.write
                wfile_flush = self.wfile.flush
                while True:
                    line = resp.readline()
                    if not line:
                        break
                    wfile_write(line)
                    wfile_flush()
                    # Hot-path optimization: only JSON-decode final usage/timings SSE frames (skips ~99% of token chunks)
                    if line.startswith(b"data:") and (b'"usage"' in line or b'"timings"' in line):
                        payload = line[5:].strip()
                        if payload and payload != b"[DONE]":
                            try:
                                ev = json.loads(payload.decode("utf-8", errors="ignore"))
                                if isinstance(ev, dict):
                                    u = ev.get("usage")
                                    if isinstance(u, dict):
                                        prompt_tok = int(u.get("prompt_tokens") or u.get("input_tokens") or prompt_tok)
                                        gen_tok = int(u.get("completion_tokens") or u.get("output_tokens") or gen_tok)
                                    tm = ev.get("timings")
                                    if isinstance(tm, dict) and tm.get("predicted_per_second"):
                                        tps = float(tm["predicted_per_second"])
                            except Exception:
                                pass
                reusable = (not resp.will_close) and resp.isclosed()
            else:
                data = resp.read()
                reusable = not resp.will_close
                try:
                    obj = json.loads(data.decode("utf-8", errors="ignore"))
                    if isinstance(obj, dict):
                        u = obj.get("usage") or {}
                        prompt_tok = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
                        gen_tok = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
                        tm = obj.get("timings") or {}
                        if tm.get("predicted_per_second"):
                            tps = float(tm["predicted_per_second"])
                except Exception:
                    pass
                self.send_response(status_code)
                self._cors_headers()
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except Exception as e:
            self.mgr.invalidate_cache()
            status_code = 502
            try:
                self._send_json({"error": {"message": f"Gateway proxy error: {e}"}}, status=502)
            except Exception:
                pass
        finally:
            if conn is not None:
                if reusable:
                    self.mgr._pool.release(bport, conn)
                else:
                    self.mgr._pool.discard(conn)
            self.mgr._inc_active(-1)
            dt = time.perf_counter() - t0
            if tps == 0.0 and gen_tok > 0 and dt > 0:
                tps = gen_tok / dt
            self.mgr.record_request({
                "time": datetime.datetime.now().strftime("%H:%M:%S"),
                "protocol": proto_label,
                "method": "POST",
                "path": backend_path,
                "client": self.client_address[0],
                "model": req_model,
                "status": status_code,
                "prompt_tok": prompt_tok,
                "gen_tok": gen_tok,
                "tps": round(tps, 1),
                "elapsed_s": round(dt, 2),
                "auto_woke": auto_woke,
            })

    # ---------- Legacy OpenAI /v1/completions Adapter ----------
    def _handle_legacy_completions(self) -> None:
        t0 = time.perf_counter()
        raw = self._read_raw_body()
        try:
            req_obj = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            return self._send_json({"error": {"message": "Invalid JSON"}}, status=400)

        prompt = req_obj.get("prompt", "")
        if isinstance(prompt, list):
            prompt = "\n".join(str(x) for x in prompt)
        model = str(req_obj.get("model") or MODEL_PRIMARY)
        stream = bool(req_obj.get("stream"))

        active_key = self._active_model_info()["key"]
        chat_payload = {
            "model": active_key,
            "messages": [{"role": "user", "content": str(prompt)}],
            "stream": stream,
        }
        for k in ("max_tokens", "temperature", "top_p", "stop"):
            if k in req_obj and req_obj[k] is not None:
                chat_payload[k] = req_obj[k]
        if stream:
            chat_payload["stream_options"] = {"include_usage": True}

        self.mgr._inc_active(1)
        auto_woke = False
        prompt_tok = gen_tok = 0
        tps = 0.0
        status_code = 200
        conn: http.client.HTTPConnection | None = None
        reusable = False
        bport = int(self.mgr.config.get("backend_port") or 8081)
        try:
            ok, bport, auto_woke, err = self.mgr.ensure_backend_ready()
            if not ok:
                status_code = 503
                return self._send_json({"error": {"message": err}}, status=503)

            body_bytes = json.dumps(chat_payload, ensure_ascii=False).encode("utf-8")
            conn, resp = self.mgr._pool.request(
                bport, "POST", "/v1/chat/completions",
                body=body_bytes, headers={"Content-Type": "application/json"}, timeout=300.0,
            )
            if stream:
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                while True:
                    raw_line = resp.readline()
                    if not raw_line:
                        break
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    pl = line[5:].strip()
                    if pl == "[DONE]":
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                        break
                    ev = json.loads(pl)
                    u = ev.get("usage")
                    if isinstance(u, dict):
                        prompt_tok = int(u.get("prompt_tokens") or prompt_tok)
                        gen_tok = int(u.get("completion_tokens") or gen_tok)
                    tm = ev.get("timings")
                    if isinstance(tm, dict) and tm.get("predicted_per_second"):
                        tps = float(tm["predicted_per_second"])
                    ch = ev.get("choices") or []
                    text = ((ch[0].get("delta") or {}).get("content") or "") if ch else ""
                    fin = ch[0].get("finish_reason") if ch else None
                    out = {
                        "id": ev.get("id", "cmpl-strata"),
                        "object": "text_completion",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{"text": text, "index": 0, "finish_reason": fin}],
                    }
                    self.wfile.write(f"data: {json.dumps(out, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                reusable = (not resp.will_close) and resp.isclosed()
            else:
                obj = json.loads(resp.read().decode("utf-8"))
                reusable = not resp.will_close
                u = obj.get("usage") or {}
                prompt_tok = int(u.get("prompt_tokens") or 0)
                gen_tok = int(u.get("completion_tokens") or 0)
                tm = obj.get("timings") or {}
                if tm.get("predicted_per_second"):
                    tps = float(tm["predicted_per_second"])
                ch = (obj.get("choices") or [{}])[0]
                content = (ch.get("message") or {}).get("content") or ""
                self._send_json({
                    "id": obj.get("id", "cmpl-strata"),
                    "object": "text_completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"text": content, "index": 0, "finish_reason": ch.get("finish_reason") or "stop"}],
                    "usage": u,
                })
        except Exception as e:
            self.mgr.invalidate_cache()
            status_code = 502
            self._send_json({"error": {"message": str(e)}}, status=502)
        finally:
            if conn is not None:
                if reusable:
                    self.mgr._pool.release(bport, conn)
                else:
                    self.mgr._pool.discard(conn)
            self.mgr._inc_active(-1)
            dt = time.perf_counter() - t0
            self.mgr.record_request({
                "time": datetime.datetime.now().strftime("%H:%M:%S"),
                "protocol": "OpenAI-Legacy",
                "method": "POST",
                "path": "/v1/completions",
                "client": self.client_address[0],
                "model": model,
                "status": status_code,
                "prompt_tok": prompt_tok,
                "gen_tok": gen_tok,
                "tps": round(tps or (gen_tok / dt if dt > 0 else 0.0), 1),
                "elapsed_s": round(dt, 2),
                "auto_woke": auto_woke,
            })

    # ---------- Ollama /api/chat & /api/generate Adapters ----------
    def _ollama_to_openai_chat(self, req_obj: dict, is_generate: bool) -> dict:
        opts = req_obj.get("options") if isinstance(req_obj.get("options"), dict) else {}
        if is_generate:
            msgs = []
            if req_obj.get("system"):
                msgs.append({"role": "system", "content": str(req_obj["system"])})
            msgs.append({"role": "user", "content": str(req_obj.get("prompt") or "")})
        else:
            msgs = list(req_obj.get("messages") or [])

        stream = req_obj.get("stream", True) is not False
        payload: dict = {
            "model": self._active_model_info()["key"],
            "messages": msgs,
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if "temperature" in opts:
            payload["temperature"] = opts["temperature"]
        if "top_p" in opts:
            payload["top_p"] = opts["top_p"]
        if "num_predict" in opts and int(opts["num_predict"]) > 0:
            payload["max_tokens"] = int(opts["num_predict"])
        if "stop" in opts:
            payload["stop"] = opts["stop"]
        if req_obj.get("tools"):
            payload["tools"] = req_obj["tools"]
        if req_obj.get("format") == "json":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _run_ollama_adapter(self, is_generate: bool) -> None:
        t0 = time.perf_counter()
        raw = self._read_raw_body()
        try:
            req_obj = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            return self._send_json({"error": "invalid json"}, status=400)

        req_model = str(req_obj.get("model") or "inkling-2-mini:latest")
        stream = req_obj.get("stream", True) is not False
        chat_payload = self._ollama_to_openai_chat(req_obj, is_generate=is_generate)
        endpoint_path = "/api/generate" if is_generate else "/api/chat"

        self.mgr._inc_active(1)
        auto_woke = False
        prompt_tok = gen_tok = 0
        prompt_ms = gen_ms = 0.0
        tps = 0.0
        status_code = 200
        conn: http.client.HTTPConnection | None = None
        reusable = False
        bport = int(self.mgr.config.get("backend_port") or 8081)
        try:
            ok, bport, auto_woke, err = self.mgr.ensure_backend_ready()
            if not ok:
                status_code = 503
                return self._send_json({"error": err}, status=503)

            body_bytes = json.dumps(chat_payload, ensure_ascii=False).encode("utf-8")
            conn, resp = self.mgr._pool.request(
                bport, "POST", "/v1/chat/completions",
                body=body_bytes, headers={"Content-Type": "application/json"}, timeout=300.0,
            )
            if stream:
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                accum_tool_calls: dict[int, dict] = {}
                while True:
                    raw_line = resp.readline()
                    if not raw_line:
                        break
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    pl = line[5:].strip()
                    if pl == "[DONE]":
                        break
                    ev = json.loads(pl)
                    u = ev.get("usage")
                    if isinstance(u, dict):
                        prompt_tok = int(u.get("prompt_tokens") or prompt_tok)
                        gen_tok = int(u.get("completion_tokens") or gen_tok)
                    tm = ev.get("timings")
                    if isinstance(tm, dict):
                        prompt_ms = float(tm.get("prompt_ms") or prompt_ms)
                        gen_ms = float(tm.get("predicted_ms") or gen_ms)
                        if tm.get("predicted_per_second"):
                            tps = float(tm["predicted_per_second"])
                    ch = ev.get("choices") or []
                    if not ch:
                        continue
                    delta = ch[0].get("delta") or {}
                    piece = delta.get("content") or ""
                    if delta.get("tool_calls"):
                        for tc in delta["tool_calls"]:
                            idx = int(tc.get("index", 0))
                            cur = accum_tool_calls.setdefault(idx, {"function": {"name": "", "arguments": ""}})
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                cur["function"]["name"] += fn["name"]
                            if fn.get("arguments"):
                                cur["function"]["arguments"] += fn["arguments"]
                    if piece:
                        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                        if is_generate:
                            chunk = {"model": req_model, "created_at": now_iso, "response": piece, "done": False}
                        else:
                            chunk = {
                                "model": req_model,
                                "created_at": now_iso,
                                "message": {"role": "assistant", "content": piece},
                                "done": False,
                            }
                        self.wfile.write((json.dumps(chunk, ensure_ascii=False) + "\n").encode("utf-8"))
                        self.wfile.flush()

                reusable = (not resp.will_close) and resp.isclosed()
                # Emit final done=true NDJSON frame with Ollama timing fields
                total_ns = int((time.perf_counter() - t0) * 1e9)
                now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                final_msg: dict = {"role": "assistant", "content": ""}
                if accum_tool_calls and not is_generate:
                    final_tcs = []
                    for _, tc in sorted(accum_tool_calls.items()):
                        args_raw = tc["function"]["arguments"]
                        try:
                            args_val = json.loads(args_raw) if args_raw else {}
                        except Exception:
                            args_val = {"raw": args_raw}
                        final_tcs.append({"function": {"name": tc["function"]["name"], "arguments": args_val}})
                    final_msg["tool_calls"] = final_tcs
                done_obj: dict = {
                    "model": req_model,
                    "created_at": now_iso,
                    "done": True,
                    "done_reason": "stop",
                    "total_duration": total_ns,
                    "load_duration": 0,
                    "prompt_eval_count": prompt_tok,
                    "prompt_eval_duration": int(prompt_ms * 1e6),
                    "eval_count": gen_tok,
                    "eval_duration": int(gen_ms * 1e6) if gen_ms > 0 else total_ns,
                }
                if is_generate:
                    done_obj["response"] = ""
                else:
                    done_obj["message"] = final_msg
                self.wfile.write((json.dumps(done_obj, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
            else:
                obj = json.loads(resp.read().decode("utf-8"))
                reusable = not resp.will_close
                u = obj.get("usage") or {}
                prompt_tok = int(u.get("prompt_tokens") or 0)
                gen_tok = int(u.get("completion_tokens") or 0)
                tm = obj.get("timings") or {}
                prompt_ms = float(tm.get("prompt_ms") or 0.0)
                gen_ms = float(tm.get("predicted_ms") or 0.0)
                if tm.get("predicted_per_second"):
                    tps = float(tm["predicted_per_second"])
                ch = (obj.get("choices") or [{}])[0]
                msg = ch.get("message") or {}
                content = msg.get("content") or ""
                total_ns = int((time.perf_counter() - t0) * 1e9)
                now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                out_obj: dict = {
                    "model": req_model,
                    "created_at": now_iso,
                    "done": True,
                    "done_reason": ch.get("finish_reason") or "stop",
                    "total_duration": total_ns,
                    "load_duration": 0,
                    "prompt_eval_count": prompt_tok,
                    "prompt_eval_duration": int(prompt_ms * 1e6),
                    "eval_count": gen_tok,
                    "eval_duration": int(gen_ms * 1e6) if gen_ms > 0 else total_ns,
                }
                if is_generate:
                    out_obj["response"] = content
                else:
                    out_msg: dict = {"role": "assistant", "content": content}
                    if msg.get("tool_calls"):
                        out_tcs = []
                        for tc in msg["tool_calls"]:
                            fn = tc.get("function") or {}
                            raw_a = fn.get("arguments")
                            try:
                                parsed_a = json.loads(raw_a) if isinstance(raw_a, str) else (raw_a or {})
                            except Exception:
                                parsed_a = {"raw": raw_a}
                            out_tcs.append({"function": {"name": fn.get("name", ""), "arguments": parsed_a}})
                        out_msg["tool_calls"] = out_tcs
                    out_obj["message"] = out_msg
                self._send_json(out_obj)
        except Exception as e:
            self.mgr.invalidate_cache()
            status_code = 502
            try:
                self._send_json({"error": str(e)}, status=502)
            except Exception:
                pass
        finally:
            if conn is not None:
                if reusable:
                    self.mgr._pool.release(bport, conn)
                else:
                    self.mgr._pool.discard(conn)
            self.mgr._inc_active(-1)
            dt = time.perf_counter() - t0
            self.mgr.record_request({
                "time": datetime.datetime.now().strftime("%H:%M:%S"),
                "protocol": "Ollama",
                "method": "POST",
                "path": endpoint_path,
                "client": self.client_address[0],
                "model": req_model,
                "status": status_code,
                "prompt_tok": prompt_tok,
                "gen_tok": gen_tok,
                "tps": round(tps or (gen_tok / dt if dt > 0 else 0.0), 1),
                "elapsed_s": round(dt, 2),
                "auto_woke": auto_woke,
            })

    def _handle_ollama_chat(self) -> None:
        self._run_ollama_adapter(is_generate=False)

    def _handle_ollama_generate(self) -> None:
        self._run_ollama_adapter(is_generate=True)


def run_standalone() -> None:
    """Headless standalone entrypoint (used by Start-Gateway.cmd)."""
    sys.path.insert(0, str(ROOT / "gui"))
    import app as gui_app

    mgr = AgentGatewayManager(gui_app.get_status, gui_app.start_model, gui_app.stop_model)
    res = mgr.start()
    if not res.get("ok"):
        print("Failed to start Agent Gateway:", res.get("error"), flush=True)
        sys.exit(1)
    st = mgr.status()
    print(f"Strata Inkling Open Agent Gateway listening on {st['host']}:{st['port']}", flush=True)
    print(f"  OpenAI / LM Studio : {st['endpoints']['openai']}", flush=True)
    print(f"  Ollama Compatible  : {st['endpoints']['ollama']}", flush=True)
    print(f"  Anthropic Messages : {st['endpoints']['anthropic']}/v1/messages", flush=True)
    print(f"  Auto-Wake on call  : {st['auto_wake']}", flush=True)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        mgr.stop()


if __name__ == "__main__":
    run_standalone()
