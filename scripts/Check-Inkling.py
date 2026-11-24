import json
import sys
import time
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8081
BASE = f"http://127.0.0.1:{PORT}"

def check():
    with urllib.request.urlopen(f"{BASE}/health", timeout=5) as r:
        health = json.loads(r.read().decode("utf-8"))
    print("Health:", health)

    with urllib.request.urlopen(f"{BASE}/v1/models", timeout=5) as r:
        models = json.loads(r.read().decode("utf-8"))
    print("Models:", [m["id"] for m in models.get("data", [])])

    cases = [
        ("zh_tw", "請用繁體中文簡要介紹 RX 6700 XT 跑 MoE 模型的優勢（約 80 字）。", 160, 1.0),
        ("code", "Write a concise Python binary search function with type hints.", 160, 1.0),
    ]
    for name, prompt, max_tok, temp in cases:
        body = json.dumps({
            "model": "inkling-2-mini-sq-10",
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

if __name__ == "__main__":
    check()
