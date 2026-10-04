"""Polite fetching and temp-folder rules for the planning layer index (round of 2 Oct 2026).

- Every download goes to %TEMP%\\qnit_planning\\<area>\\ only, is checked against its sha256 while
  streaming, and is deleted by the caller as soon as it has been used.
- At most one download per host at a time (a lock per host), an honest User-Agent, and
  exponential backoff on errors. Bytes and requests are counted per host (`STATS`).
- Overpass answers are kept in memory only; mirrors are rotated with backoff.
- `TempArea` marks a sub-folder as in use (a .lock file with the owner's PID) so that the
  planning service's wipe at start/stop skips folders of a running job.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "qnit-builders-planning/1.18 (+https://builder.qnit.site; master-plan layer index; "
    "one request at a time)"
)
TEMP_ROOT = os.path.join(
    os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp", "qnit_planning"
)
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

STATS: dict[str, dict[str, float]] = {}
_HOST_LOCKS: dict[str, threading.RLock] = {}
_GUARD = threading.Lock()
# our own requests use the original urlopen; install_urlopen_counter() wraps the global one
# for the older scripts, and must not wrap (and re-lock / re-count) these
_URLOPEN = urllib.request.urlopen


class SourceChanged(Exception):
    """The downloaded file's sha256 differs from the indexed one."""


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or "?"


def _lock(host: str) -> threading.RLock:
    with _GUARD:
        return _HOST_LOCKS.setdefault(host, threading.RLock())


def _count(host: str, nbytes: int) -> None:
    with _GUARD:
        s = STATS.setdefault(host, {"requests": 0, "bytes": 0})
        s["requests"] += 1
        s["bytes"] += nbytes


def stats_mb() -> dict[str, dict[str, float]]:
    return {
        h: {"requests": int(v["requests"]), "mb": round(v["bytes"] / 1e6, 2)}
        for h, v in sorted(STATS.items())
    }


def dir_bytes(path: str = TEMP_ROOT) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


class PeakMeter:
    """Samples the temp folder size every `every` seconds in a thread; `.peak` in bytes."""

    def __init__(self, path: str = TEMP_ROOT, every: float = 1.0):
        self.path, self.every, self.peak = path, every, 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, dir_bytes(self.path))
            self._stop.wait(self.every)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()
        self.peak = max(self.peak, dir_bytes(self.path))


class TempArea:
    """A sub-folder of TEMP_ROOT owned by this process; removed on exit."""

    def __init__(self, name: str):
        self.path = os.path.join(TEMP_ROOT, name)

    def __enter__(self) -> str:
        os.makedirs(self.path, exist_ok=True)
        with open(os.path.join(self.path, ".lock"), "w") as f:
            f.write(str(os.getpid()))
        return self.path

    def __exit__(self, *exc):
        # a script that exits with a PDF still open keeps a Windows file handle: collect,
        # retry, and as a last resort delete at process exit
        import atexit
        import gc

        for _ in range(5):
            gc.collect()
            shutil.rmtree(self.path, ignore_errors=True)
            if not os.path.exists(self.path):
                return
            time.sleep(1)
        atexit.register(shutil.rmtree, self.path, True)


def _open(url: str, data: bytes | None = None, timeout: float = 300):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    return _URLOPEN(req, timeout=timeout)


def download(
    url: str,
    dest_dir: str,
    name: str,
    sha256: str | None = None,
    tries: int = 6,
) -> tuple[str, str]:
    """Stream `url` to dest_dir/name, one request per host at a time. Returns (path, sha256).
    Raises SourceChanged when sha256 is given and differs (the file is deleted)."""
    host = _host(url)
    path = os.path.join(dest_dir, name)
    last: Exception | None = None
    for attempt in range(tries):
        with _lock(host):
            try:
                h = hashlib.sha256()
                n = 0
                with _open(url, timeout=600) as r, open(path + ".part", "wb") as f:
                    while True:
                        b = r.read(1 << 20)
                        if not b:
                            break
                        f.write(b)
                        h.update(b)
                        n += len(b)
                _count(host, n)
                os.replace(path + ".part", path)
                got = h.hexdigest()
                if sha256 and got != sha256:
                    os.remove(path)
                    raise SourceChanged(f"{url}: sha256 {got} != indexed {sha256}")
                return path, got
            except SourceChanged:
                raise
            except Exception as ex:  # noqa: BLE001 - retried with backoff
                last = ex
                _count(host, 0)
                for p in (path + ".part", path):
                    if os.path.exists(p):
                        os.remove(p)
        time.sleep(min(300, 5 * 2**attempt))
    raise RuntimeError(f"download failed after {tries} tries: {url}: {last}")


def overpass(query: str, timeout_s: int = 240, tries: int = 9) -> list[dict]:
    """Overpass `out geom` elements for an [out:json] query body, in memory only."""
    body = urllib.parse.urlencode(
        {"data": f"[out:json][timeout:{timeout_s}];{query}out geom;"}
    ).encode()
    last: Exception | None = None
    for attempt in range(tries):
        ep = OVERPASS_MIRRORS[attempt % len(OVERPASS_MIRRORS)]
        host = _host(ep)
        with _lock(host):
            t0 = time.time()
            try:
                with _open(ep, data=body, timeout=timeout_s + 30) as r:
                    raw = r.read()
                _count(host, len(raw))
                print(
                    f"    overpass {host} ok {len(raw) / 1e6:.2f} MB {time.time() - t0:.0f}s",
                    flush=True,
                )
                return json.loads(raw)["elements"]
            except Exception as ex:  # noqa: BLE001 - next mirror after backoff
                last = ex
                _count(host, 0)
                print(
                    f"    overpass {host} failed after {time.time() - t0:.0f}s: {str(ex)[:120]}",
                    flush=True,
                )
        time.sleep(min(240, 10 * 2 ** (attempt // len(OVERPASS_MIRRORS))))
    raise RuntimeError(f"Overpass failed on every mirror: {last}")


def install_urlopen_counter() -> None:
    """Route urllib.request.urlopen (used by the older calibration scripts) through the
    per-host lock, the honest User-Agent, the per-host counters and backoff: a 429 or 5xx is
    retried on the same host after 30, 60 and 120 s before the script sees the error."""
    orig = _URLOPEN

    def counted(req, *a, **kw):
        if isinstance(req, str):
            req = urllib.request.Request(req)
        req.headers["User-agent"] = USER_AGENT
        host = _host(req.full_url)
        for attempt in range(4):
            try:
                with _lock(host):
                    resp = orig(req, *a, **kw)
                    data = resp.read()
                break
            except urllib.error.HTTPError as ex:
                _count(host, 0)
                if attempt == 3 or not (ex.code == 429 or ex.code >= 500):
                    raise
                time.sleep(30 * 2**attempt)
        _count(host, len(data))

        class _R:
            def __init__(self, d):
                self._d = d

            def read(self, *_):
                d, self._d = self._d, b""
                return d

            def __enter__(self):
                return self

            def __exit__(self, *e):
                return False

        return _R(data)

    urllib.request.urlopen = counted
