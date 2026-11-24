"""SHA-256 verifier with automatic chunked HTTP-Range self-repair for .strata model packs."""
from __future__ import annotations

import concurrent.futures
import hashlib
import os
import struct
import sys
import threading
import time
from pathlib import Path

import requests

CHUNK_SIZE = 32 * 1024 * 1024  # 32 MiB
WORKERS = 4


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while buf := f.read(16 * 1024 * 1024):
            h.update(buf)
    return h.hexdigest()


def parse_strata_sections(path: Path) -> list[tuple[int, int, str]]:
    with open(path, "rb") as f:
        hdr = f.read(16 + 6 * 64)
    if len(hdr) < 16 or not hdr.startswith(b"STRATA"):
        return []
    _, _, _, _, nsec = struct.unpack("<8sHHHH", hdr[:16])
    out = []
    for i in range(min(nsec, 6)):
        off, sz, sha_b = struct.unpack("<QQ32s", hdr[16 + i * 64 : 16 + i * 64 + 48])
        out.append((off, sz, sha_b.hex()))
    return out


def fetch_range(direct_url: str, fallback_url: str, start: int, length: int) -> bytes:
    for attempt in range(5):
        try:
            headers = {"Range": f"bytes={start}-{start + length - 1}"}
            r = requests.get(direct_url, headers=headers, timeout=60)
            if r.status_code in (401, 403):
                r = requests.get(fallback_url, headers=headers, allow_redirects=True, timeout=60)
            r.raise_for_status()
            if len(r.content) == length:
                return r.content
        except Exception:
            if attempt == 4:
                raise
            time.sleep(1 + attempt)
    raise RuntimeError(f"Range fetch failed at offset {start} (+{length})")


def verify_or_repair(file_path: Path, url: str, expected_sha: str, expected_size: int) -> bool:
    if not file_path.is_file():
        print(f"[ERROR] File not found: {file_path}", flush=True)
        return False

    actual_size = file_path.stat().st_size
    if actual_size != expected_size:
        print(f"[ERROR] Size mismatch: got {actual_size}, expected {expected_size}", flush=True)
        return False

    print("[VERIFY] Computing full-file SHA-256...", flush=True)
    got_sha = sha256_file(file_path)
    if got_sha == expected_sha:
        print(f"[OK] SHA-256 verified: {got_sha}", flush=True)
        return True

    print(f"[WARN] SHA-256 mismatch (got {got_sha[:16]}..., expected {expected_sha[:16]}...)", flush=True)
    print("[REPAIR] Starting automatic chunked HTTP-Range verification & self-repair...", flush=True)

    sections = parse_strata_sections(file_path)
    bad_ranges: list[tuple[int, int]] = []
    if sections:
        with open(file_path, "rb") as f:
            for idx, (off, sz, sec_exp) in enumerate(sections):
                if sz == 0:
                    continue
                f.seek(off)
                h = hashlib.sha256()
                rem = sz
                while rem > 0:
                    c = f.read(min(16 * 1024 * 1024, rem))
                    h.update(c)
                    rem -= len(c)
                if h.hexdigest() != sec_exp:
                    print(f"[REPAIR] Section {idx} mismatched ({sz / 1e6:.1f} MB); scanning chunks...", flush=True)
                    bad_ranges.append((off, sz))
    if not bad_ranges:
        bad_ranges = [(0, expected_size)]

    r_head = requests.head(url, allow_redirects=True, timeout=30)
    r_head.raise_for_status()
    direct_url = r_head.url

    chunks: list[tuple[int, int]] = []
    for base_off, total_sz in bad_ranges:
        pos = base_off
        end = base_off + total_sz
        while pos < end:
            c_end = min(end, pos + CHUNK_SIZE)
            chunks.append((pos, c_end - pos))
            pos = c_end

    lock = threading.Lock()
    done = 0
    stuck_patches: dict[int, int] = {}

    def check_chunk(pair):
        nonlocal done
        idx, (start, length) = pair
        net = fetch_range(direct_url, url, start, length)
        with open(file_path, "rb") as f:
            f.seek(start)
            disk = f.read(length)
        if net != disk:
            net2 = fetch_range(direct_url, url, start, length)
            good = net if net == net2 else fetch_range(direct_url, url, start, length)
            with lock:
                print(f"[REPAIR] Fixing differing chunk {idx} at offset {start}...", flush=True)
                with open(file_path, "r+b") as wf:
                    wf.seek(start)
                    wf.write(good)
                    wf.flush()
                    os.fsync(wf.fileno())
                with open(file_path, "rb") as rf:
                    rf.seek(start)
                    rb = rf.read(length)
                if rb != good:
                    print(f"[WARN] Disk read-back mismatch on chunk {idx}; scheduling clean relocation...", flush=True)
                    for i in range(length):
                        if rb[i] != good[i]:
                            stuck_patches[start + i] = good[i]
        with lock:
            done += 1
            if done % 30 == 0 or done == len(chunks):
                print(f"[REPAIR] Scanned {done}/{len(chunks)} chunks...", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(check_chunk, enumerate(chunks)))

    if stuck_patches:
        print(f"[REPAIR] Relocating file to fresh disk blocks ({len(stuck_patches)} byte patch(es))...", flush=True)
        reloc = file_path.with_suffix(file_path.suffix + ".clean")
        with open(file_path, "rb") as fin, open(reloc, "wb") as fout:
            off = 0
            while True:
                buf = bytearray(fin.read(16 * 1024 * 1024))
                if not buf:
                    break
                end = off + len(buf)
                for p_off, p_val in stuck_patches.items():
                    if off <= p_off < end:
                        buf[p_off - off] = p_val
                fout.write(buf)
                off = end
            fout.flush()
            os.fsync(fout.fileno())
        if sha256_file(reloc) == expected_sha:
            os.replace(reloc, file_path)
            print(f"[OK] Relocated file verified 100%: {expected_sha}", flush=True)
            return True
        print("[ERROR] Relocated file still failed SHA-256 verification.", flush=True)
        return False

    final_sha = sha256_file(file_path)
    if final_sha == expected_sha:
        print(f"[OK] Self-repair succeeded! SHA-256: {final_sha}", flush=True)
        return True
    print(f"[ERROR] Still mismatched after repair: {final_sha}", flush=True)
    return False


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: Verify-Download.py <file_path> <url> <expected_sha256> <expected_bytes>")
        sys.exit(2)
    ok = verify_or_repair(Path(sys.argv[1]), sys.argv[2], sys.argv[3].lower(), int(sys.argv[4]))
    sys.exit(0 if ok else 1)
