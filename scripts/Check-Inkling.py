#!/usr/bin/env python3
"""End-to-end health, speed, and multi-protocol verification script for Strata Inkling-2-Mini."""
from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8081
GW_PORT = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 11435
BASE = f"http://127.0.0.1:{PORT}"
GW_BASE = f"http://127.0.0.1:{GW_PORT}"


def _get_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check() -> None:
    health = _get_json(f"{BASE}/health")
    print("Health:", health)

    models_obj = _get_json(f"{BASE}/v1/models")
    model_ids = [m["id"] for m in models_obj.get("data", [])]
    model_id = model_ids[0] if model_ids else "inkling-2-mini-sq-15"
    print("Models:", model_ids)

    cases = [
        ("zh_tw", "請用繁體中文簡要介紹 Mixture of Experts（MoE）模型在本機運作的三大優勢（約 80 字）。", 160, 1.0),
        ("code", "Write a concise Python binary search function with type hints.", 160, 0.2),
    ]
    for name, prompt, max_tok, temp in cases:
        body = json.dumps({
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tok,
            "temperature": temp,
            "stream": True,
            "stream_options": {"include_usage": True},
        }).encode("utf-8")
        req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body, headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        t_first = None
        chunks = []
        usage = timings = None
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                ev = json.loads(payload)
                if ev.get("usage"):
                    usage = ev["usage"]
                if ev.get("timings"):
                    timings = ev["timings"]
                ch = ev.get("choices") or []
                if ch:
                    piece = (ch[0].get("delta") or {}).get("content") or ""
                    if piece:
                        if t_first is None:
                            t_first = time.perf_counter()
                        chunks.append(piece)
        t1 = time.perf_counter()
        text = "".join(chunks)
        tps = (timings or {}).get("predicted_per_second") or 0
        pps = (timings or {}).get("prompt_per_second") or 0
        ctok = (usage or {}).get("completion_tokens") or 0
        print(f"\n[{name}] gen={ctok} tok | prefill={pps:.1f} tok/s | decode={tps:.1f} tok/s | ttft={(t_first - t0) if t_first else (t1 - t0):.2f}s")
        print(text[:240].replace("\n", " "))

    # Optional Open Agent Gateway verification if listening
    try:
        gw_h = _get_json(f"{GW_BASE}/health", timeout=2.0)
    except Exception:
        return

    print(f"\n--- Open Agent Gateway ({GW_BASE}) ---")
    print("Gateway Health:", gw_h)
    lats = []
    for _ in range(15):
        ts = time.perf_counter()
        _get_json(f"{GW_BASE}/health", timeout=2.0)
        lats.append((time.perf_counter() - ts) * 1000.0)
    print(f"Gateway /health latency: median={statistics.median(lats):.2f} ms | max={max(lats):.2f} ms")

    # Verify Ollama /api/chat
    ol_req = urllib.request.Request(
        f"{GW_BASE}/api/chat",
        data=json.dumps({
            "model": "inkling-2-mini:latest",
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "stream": False,
            "options": {"num_predict": 16, "temperature": 0.0},
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(ol_req, timeout=60) as r:
        ol_resp = json.loads(r.read().decode("utf-8"))
    print("Ollama /api/chat:", (ol_resp.get("message") or {}).get("content", "").strip()[:80])

    # Verify Anthropic /v1/messages
    an_req = urllib.request.Request(
        f"{GW_BASE}/v1/messages",
        data=json.dumps({
            "model": model_id,
            "max_tokens": 24,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        }).encode("utf-8"),
        headers={"Content-Type": "application/json", "anthropic-version": "2023-06-01"},
    )
    with urllib.request.urlopen(an_req, timeout=60) as r:
        an_resp = json.loads(r.read().decode("utf-8"))
    content_blocks = an_resp.get("content") or []
    an_txt = "".join(b.get("text", "") for b in content_blocks if isinstance(b, dict)).strip()
    print("Anthropic /v1/messages:", an_txt[:80])


if __name__ == "__main__":
    check()
