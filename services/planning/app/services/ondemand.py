# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""On-demand zone layers from the layer index (contract 1.18).

Nothing is read from disk but the index (infra/planning/layer_index.json, text only). A query
names the sheets it needs by extent; a sheet that is not in memory is downloaded to
%TEMP%\\qnit_planning\\svc\\ (sha256 checked against the index), extracted by the sheet worker
in a subprocess capped at PLANNING_WORKER_CAP_MB (stored calibration, no network), and kept in
RAM as zstd-compressed Arrow chunks; the download is deleted straight away. Evicted sheets are
fetched again on the next view.

Priority ("the most detailed sheet wins"): within a plan, rows are ordered by (rank, order)
as in the original merge. A sheet's pieces are clipped to the plan's LPA outline (when the
row says so) and cut by the footprints of every higher-priority sheet that overlaps them;
LPA area on no sheet is the plan's `uncovered` zone. Merged results are computed per 2 km
chunk and cached, so /zones and /zones/at read the same pieces under stable zone_uids.
"""

from __future__ import annotations

import collections
import json
import logging
import math
import os
import shutil
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import geopandas as gpd
import httpx
import numpy as np
import pyarrow as pa
import shapely

from app.services import winjob

log = logging.getLogger(__name__)

CRS_METRIC = 32643
# GEOS objects in the shared caches (prepared outlines, lazily built STRtrees) must not be
# used from two request threads at once (access violation seen 3 Oct): every request's work
# on them runs under this lock; the fetch thread only adds new objects
COMPUTE = threading.RLock()
_SSL_CONTEXT = ssl.create_default_context()
CHUNK_M = 2000.0
UNCOVERED_TILE_M = 2000.0
USER_AGENT = (
    "qnit-builders-planning/1.18 (+https://builder.qnit.site; master-plan layer index; "
    "one request at a time)"
)
# PLANNING_TEMP_ROOT isolates a test run from a live service's temp folder
TEMP_ROOT = os.getenv("PLANNING_TEMP_ROOT") or os.path.join(
    os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp", "qnit_planning"
)
SVC_TEMP = os.path.join(TEMP_ROOT, "svc")
OVERLAY_CLASS = {
    "ngt_buffer": "ngt_buffer",
    "forest_symbol_area": "forest_symbol",
    "stream_centreline": "stream_centreline",
}


def _env_mb(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default)) << 20
    except ValueError:
        return default << 20


CACHE_CAP = _env_mb(
    "PLANNING_CACHE_MB", 700
)  # <= 800 MB (brief); headroom for requests
WORKER_CAP = _env_mb("PLANNING_WORKER_CAP_MB", 2048)


# ---------------------------------------------------------------- temp folder
class memtrace:
    """PLANNING_DEBUG_MEM=1: log the service's committed memory growth over a block when it
    exceeds 30 MB (=2 also lists the top Python allocation sites, via tracemalloc)."""

    level = int(os.getenv("PLANNING_DEBUG_MEM", "0") or 0)

    def __init__(self, label: str):
        self.label = label

    def __enter__(self):
        if self.level:
            import tracemalloc

            if self.level > 1 and not tracemalloc.is_tracing():
                tracemalloc.start(8)
            self.c0 = winjob.process_peak_bytes(os.getpid())[0]
            self.s0 = tracemalloc.take_snapshot() if self.level > 1 else None
        return self

    def __exit__(self, *exc):
        if not self.level:
            return False
        c1 = winjob.process_peak_bytes(os.getpid())[0]
        d = (c1 - self.c0) / 1e6
        if d > 30:
            msg = f"planning: mem +{d:.0f} MB (now {c1 / 1e6:.0f}) in {self.label}"
            if self.s0 is not None:
                import tracemalloc

                top = tracemalloc.take_snapshot().compare_to(self.s0, "traceback")[:4]
                for st in top:
                    msg += f"\n   {st.size_diff / 1e6:+.1f} MB " + " < ".join(
                        f"{f.filename.rsplit(os.sep, 1)[-1]}:{f.lineno}"
                        for f in list(st.traceback)[:5]
                    )
            print(msg, file=sys.stderr, flush=True)
        return False


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259  # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wipe_temp() -> int:
    """Delete everything under TEMP_ROOT except folders locked by a live process (a running
    index build or a detached job's log folder). Returns the bytes removed."""
    removed = 0
    if not os.path.isdir(TEMP_ROOT):
        return 0

    def walk_size(p):
        n = 0
        for root, _d, files in os.walk(p):
            for f in files:
                try:
                    n += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return n

    def locked(p):
        lock = os.path.join(p, ".lock")
        if not os.path.exists(lock):
            return False
        try:
            return _pid_alive(int(open(lock).read().strip() or 0))
        except (OSError, ValueError):
            return False

    for name in os.listdir(TEMP_ROOT):
        p = os.path.join(TEMP_ROOT, name)
        if os.path.isdir(p):
            if locked(p) or any(
                locked(os.path.join(p, s))
                for s in os.listdir(p)
                if os.path.isdir(os.path.join(p, s))
            ):
                continue
            removed += walk_size(p)
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                removed += os.path.getsize(p)
                os.remove(p)
            except OSError:
                pass
    return removed


def temp_bytes() -> int:
    n = 0
    for root, _d, files in os.walk(SVC_TEMP):
        for f in files:
            try:
                n += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return n


# ---------------------------------------------------------------- data model
@dataclass
class Chunk:
    bbox: tuple[float, float, float, float]
    data: bytes  # Arrow IPC stream, zstd
    n: int


@dataclass
class SheetData:
    row_id: str
    labels: dict
    zones: list[Chunk] = field(default_factory=list)
    overlays: list[Chunk] = field(default_factory=list)
    outlines: list[tuple[dict, shapely.Geometry]] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    nbytes: int = 0
    zone_tree: shapely.STRtree | None = None
    overlay_tree: shapely.STRtree | None = None


@dataclass
class RowState:
    state: str = (
        "not_loaded"  # ready / downloading / extracting / source_changed / failed
    )
    message: str | None = None
    retry_at: float = 0.0
    error: str | None = None


class LRU:
    """Byte-capped LRU (thread-safe). Pinned keys are never evicted."""

    def __init__(self, cap: int):
        self.cap, self.size = cap, 0
        self._d: collections.OrderedDict = collections.OrderedDict()
        self._lock = threading.RLock()
        self.pinned: set = set()
        self.evicted = 0

    def get(self, k):
        with self._lock:
            if k not in self._d:
                return None
            self._d.move_to_end(k)
            return self._d[k][0]

    def put(self, k, v, nbytes: int):
        with self._lock:
            if k in self._d:
                self.size -= self._d.pop(k)[1]
            self._d[k] = (v, nbytes)
            self.size += nbytes
            for kk in list(self._d):
                if self.size <= self.cap:
                    break
                if kk == k or kk in self.pinned:
                    continue
                self.size -= self._d.pop(kk)[1]
                self.evicted += 1

    def __contains__(self, k) -> bool:
        with self._lock:
            return k in self._d

    def pop(self, k):
        with self._lock:
            if k in self._d:
                self.size -= self._d.pop(k)[1]

    def keys(self):
        with self._lock:
            return list(self._d)


def _geom_bytes(g: np.ndarray) -> int:
    """Memory of a decoded frame: GEOS coordinates and rings (~32 B per coordinate) plus the
    pandas row (attribute objects, uid strings, index): measured ~2 KB per row."""
    return int(shapely.get_num_coordinates(g).sum()) * 32 + 2048 * len(g)


# ---------------------------------------------------------------- the store
class OnDemand:
    def __init__(self, index_path: str, docs: dict, plans: dict):
        with open(index_path, encoding="utf-8") as f:
            ix = json.load(f)
        self.build_id: str = ix.get("build_id") or "unknown"
        self.rows: dict[str, dict] = {r["row_id"]: r for r in ix["rows"]}
        self.plan_cfg: dict = ix.get("plans", {})
        self.docs, self.plans = docs, plans
        alter = os.getenv(
            "PLANNING_TEST_ALTER_SHA"
        )  # G8 test: one row's sha256, memory only
        if alter and alter in self.rows:
            self.rows[alter] = {**self.rows[alter], "sha256": "0" * 64}
        self.state: dict[str, RowState] = {k: RowState() for k in self.rows}
        # one budget (PLANNING_CACHE_MB, 700 MB): 50 % compressed sheets, 25 % compressed
        # derived results, 15 % decoded chunks, 10 % hot merged frames; the rest of the
        # 1 GB is headroom for request work
        self.cache = LRU(CACHE_CAP // 2)  # row_id -> SheetData (compressed)
        # derived results, computed once and kept zstd-compressed: merged (cut) chunks,
        # footprint unions, no-sheet tiles; decoding them is cheap, rebuilding is not
        self.derived = LRU(CACHE_CAP // 4)
        self.decoded = LRU(CACHE_CAP * 3 // 20)  # (row, chunk) -> gdf, footprints
        self.merged = LRU(CACHE_CAP // 10)  # hot merged frames + display text
        self._lock = threading.RLock()
        self._merge_lock = threading.Lock()
        self._want: collections.OrderedDict[str, float] = collections.OrderedDict()
        self._cv = threading.Condition(self._lock)
        self._stop = False
        self.stats = {
            "downloads": collections.Counter(),
            "bytes": collections.Counter(),
            "worker_peak_mb": 0,
            "worker_runs": 0,
            "temp_peak_mb": 0.0,
            "first_view_s": {},
            "extract_s": {},
        }
        self.by_plan: dict[str, list[dict]] = collections.defaultdict(list)
        self.outline_rows: dict[str, list[dict]] = collections.defaultdict(list)
        for r in self.rows.values():
            if r.get("status") != "indexed":
                continue
            if r["kind"] == "zones":
                self.by_plan[r["plan_id"]].append(r)
            else:
                self.outline_rows[r["plan_id"]].append(r)
        for rs in self.by_plan.values():
            rs.sort(key=lambda r: (r["priority"]["rank"], r["priority"]["order"]))
        self._host_locks: dict[str, threading.Lock] = collections.defaultdict(
            threading.Lock
        )
        os.makedirs(SVC_TEMP, exist_ok=True)
        with open(os.path.join(SVC_TEMP, ".lock"), "w") as f:
            f.write(str(os.getpid()))
        import atexit

        atexit.register(lambda: shutil.rmtree(SVC_TEMP, ignore_errors=True))
        self._thread = threading.Thread(
            target=self._loop, name="planning-fetch", daemon=True
        )
        self._thread.start()
        threading.Thread(target=self._meter, name="planning-meter", daemon=True).start()

    # ------------------------------------------------------------ public helpers
    def indexed_plans(self) -> set[str]:
        return set(self.by_plan)

    def outline_row_for(self, plan_id: str) -> dict | None:
        cfg = self.plan_cfg.get(plan_id) or {}
        rid = cfg.get("outline_row")
        return self.rows.get(rid) if rid else None

    def sheet_states(self, plan_id: str) -> list[dict]:
        out = []
        for r in self.by_plan.get(plan_id, []):
            s = self.state[r["row_id"]]
            out.append(
                {
                    "plan_id": plan_id,
                    "doc_id": r["doc_id"],
                    "sheet": r["sheet"],
                    "source_layer": r["source_layer"],
                    "state": s.state,
                    "placement_confirmed": bool(r.get("placement_confirmed", True)),
                    "extent": r["extent"]["wgs84"],
                    "source_url": r.get("source_url"),
                }
            )
        return out

    def plan_extent(self, plan_id: str) -> list[float] | None:
        ex = [r["extent"]["wgs84"] for r in self.by_plan.get(plan_id, [])]
        if not ex:
            return None
        a = np.array(ex)
        return [
            float(a[:, 0].min()),
            float(a[:, 1].min()),
            float(a[:, 2].max()),
            float(a[:, 3].max()),
        ]

    def pending_entry(self, row: dict) -> dict:
        s = self.state[row["row_id"]]
        host = urllib.parse.urlsplit(row.get("source_url") or "").hostname
        st = (
            s.state
            if s.state in ("downloading", "extracting", "source_changed", "failed")
            else "downloading"
        )
        msg = {
            "downloading": f"Downloading {row['sheet']} from {host}...",
            "extracting": f"Extracting {row['sheet']} from {host}...",
            "source_changed": "Source changed; needs re-indexing",
            "failed": f"Could not load {row['sheet']} from {host}; retrying",
        }[st]
        retry = None
        if st in ("downloading", "extracting"):
            retry = 5
        elif st == "failed":
            retry = max(5, int(s.retry_at - time.time()))
        return {
            "sheet": row["sheet"],
            "plan_id": row["plan_id"],
            "doc_id": row["doc_id"],
            "state": st,
            "source": host,
            "message": msg,
            "retry_after_s": retry,
        }

    # ------------------------------------------------------------ requesting rows
    def ensure(self, rows: list[dict]) -> list[dict]:
        """Ask for rows; returns pending entries for those not ready (ready rows: none)."""
        pend = []
        with self._lock:
            for r in rows:
                rid = r["row_id"]
                st = self.state[rid]
                if st.state == "ready" and self.cache.get(rid) is None:
                    st.state = "not_loaded"  # evicted
                if st.state == "ready":
                    continue
                if st.state == "source_changed":
                    pend.append(self.pending_entry(r))
                    continue
                if st.state in ("not_loaded", "failed") and time.time() >= st.retry_at:
                    if rid not in self._want:
                        self._want[rid] = time.time()
                    if st.state == "not_loaded":
                        st.state = "downloading"
                pend.append(self.pending_entry(r))
            if pend:
                self._cv.notify_all()
        return pend

    def sheet(self, row_id: str) -> SheetData | None:
        return self.cache.get(row_id)

    # ------------------------------------------------------------ fetch / extract loop
    def stop(self) -> None:
        with self._lock:
            self._stop = True
            self._cv.notify_all()

    def _next_doc(self):
        """The oldest wanted row's source; every wanted row of that source comes along."""
        with self._lock:
            while not self._want and not self._stop:
                self._cv.wait(timeout=5)
            if self._stop:
                return None
            rid = next(iter(self._want))
            r = self.rows[rid]
            key = (r["source_url"], r["sha256"])
            group = [
                k
                for k in self._want
                if (self.rows[k]["source_url"], self.rows[k]["sha256"]) == key
            ]
            for k in group:
                self._want.pop(k, None)
            return key, [self.rows[k] for k in group]

    def _loop(self) -> None:
        while True:
            nxt = self._next_doc()
            if nxt is None:
                return
            (url, sha), rows = nxt
            try:
                self._process(url, sha, rows)
            except Exception as ex:
                log.exception("sheet load failed")
                with self._lock:
                    for r in rows:
                        s = self.state[r["row_id"]]
                        if s.state != "source_changed":
                            s.state, s.error = "failed", str(ex)[:500]
                            s.retry_at = time.time() + 30

    def _download(self, url: str, sha: str, dest: str) -> str:
        if url.startswith("file://"):
            if os.getenv("PLANNING_ALLOW_FILE_SOURCES") != "1":
                raise RuntimeError("file:// sources are for tests only")
            import hashlib

            src = urllib.request.url2pathname(urllib.parse.urlsplit(url).path)
            shutil.copyfile(src, dest)
            with open(dest, "rb") as f:
                got = hashlib.sha256(f.read()).hexdigest()
            self.stats["downloads"]["file"] += 1
            if got != sha:
                os.remove(dest)
                raise _SourceChanged(f"sha256 {got} != indexed {sha}")
            return dest
        host = urllib.parse.urlsplit(url).hostname or "?"
        last = None
        for attempt in range(6):
            with self._host_locks[host]:
                try:
                    import hashlib

                    h, n = hashlib.sha256(), 0
                    with httpx.stream(
                        "GET",
                        url,
                        # the system trust store (as urllib uses): some government sites
                        # send no intermediate certificate, which certifi alone cannot chain
                        verify=_SSL_CONTEXT,
                        headers={"User-Agent": USER_AGENT},
                        timeout=httpx.Timeout(60.0, read=600.0),
                        follow_redirects=True,
                    ) as r:
                        r.raise_for_status()
                        with open(dest + ".part", "wb") as f:
                            for b in r.iter_bytes(1 << 20):
                                f.write(b)
                                h.update(b)
                                n += len(b)
                                self.stats["temp_peak_mb"] = max(
                                    self.stats["temp_peak_mb"], temp_bytes() / 1e6
                                )
                    self.stats["downloads"][host] += 1
                    self.stats["bytes"][host] += n
                    print(
                        f"planning: download {host} {n / 1e6:.1f} MB "
                        f"(#{self.stats['downloads'][host]} from this host)",
                        file=sys.stderr,
                        flush=True,
                    )
                    os.replace(dest + ".part", dest)
                    if h.hexdigest() != sha:
                        os.remove(dest)
                        raise _SourceChanged(f"sha256 {h.hexdigest()} != indexed {sha}")
                    return dest
                except _SourceChanged:
                    raise
                except Exception as ex:  # noqa: BLE001 - retried with backoff
                    last = ex
                    for p in (dest + ".part", dest):
                        if os.path.exists(p):
                            os.remove(p)
            time.sleep(min(120, 5 * 2**attempt))
        raise RuntimeError(f"download failed: {url}: {last}")

    def _process(self, url: str, sha: str, rows: list[dict]) -> None:
        t0 = time.time()
        work = os.path.join(SVC_TEMP, f"job_{int(t0 * 1000)}")
        os.makedirs(work, exist_ok=True)
        ext = os.path.splitext(urllib.parse.urlsplit(url).path)[1] or ".pdf"
        src = os.path.join(work, "source" + ext)
        try:
            with self._lock:
                for r in rows:
                    self.state[r["row_id"]].state = "downloading"
            try:
                self._download(url, sha, src)
            except _SourceChanged as ex:
                with self._lock:
                    for r in rows:
                        s = self.state[r["row_id"]]
                        s.state, s.error = "source_changed", str(ex)
                log.warning("source changed for %s: %s", url, ex)
                return
            todo = list(rows)
            while todo:
                r = todo.pop(0)
                rid = r["row_id"]
                with self._lock:
                    self.state[rid].state = "extracting"
                data = self._extract(r, src, work)
                with self._lock:
                    self._store(data)
                    self.state[rid].state = "ready"
                    self.stats["first_view_s"].setdefault(
                        rid, round(time.time() - t0, 1)
                    )
                    # rows of the same source asked for meanwhile ride on this download
                    more = [
                        k
                        for k in self._want
                        if (self.rows[k]["source_url"], self.rows[k]["sha256"])
                        == (url, sha)
                    ]
                    for k in more:
                        self._want.pop(k, None)
                        todo.append(self.rows[k])
            # keep the download while it is useful: sheets of this source asked for meanwhile
            # come first; then, while no other source is waiting and the cache has room, the
            # source's other sheets are extracted too (one download serves a whole atlas)
            rest = self._same_source(url, sha)
            while True:
                with self._lock:
                    if self._stop:
                        break
                    same = [
                        k
                        for k in self._want
                        if (self.rows[k]["source_url"], self.rows[k]["sha256"])
                        == (url, sha)
                    ]
                    if same:
                        r = self.rows[same[0]]
                        self._want.pop(same[0], None)
                    elif self._want:
                        break  # another source is waiting
                    else:
                        cand = [
                            x
                            for x in rest
                            if self.state[x["row_id"]].state == "not_loaded"
                        ]
                        if not cand or (
                            self.cache.size + self._estimate(cand[0]) > self.cache.cap
                        ):
                            break
                        r = cand[0]
                    self.state[r["row_id"]].state = "extracting"
                try:
                    data = self._extract(r, src, work)
                except Exception as ex:
                    with self._lock:
                        s_ = self.state[r["row_id"]]
                        s_.state, s_.error = "failed", str(ex)[:500]
                        s_.retry_at = time.time() + 300
                    log.exception("sheet failed: %s", r["row_id"])
                    continue
                with self._lock:
                    self._store(data)
                    self.state[r["row_id"]].state = "ready"
                    self.stats["first_view_s"].setdefault(
                        r["row_id"], round(time.time() - t0, 1)
                    )
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _same_source(self, url: str, sha: str) -> list[dict]:
        return [
            r
            for r in self.rows.values()
            if r.get("status") == "indexed"
            and (r.get("source_url"), r.get("sha256")) == (url, sha)
        ]

    def _estimate(self, row: dict) -> int:
        """Compressed size guess for a sheet not yet extracted: 400 bytes per unclipped
        zone from the index QA, else 20 MB."""
        n = (row.get("qa") or {}).get("zones_unclipped") or (row.get("qa") or {}).get(
            "zones"
        )
        return int(n) * 400 if n else 20 << 20

    def _worker_cmd(self) -> list[str]:
        py = os.getenv("PLANNING_WORKER_PYTHON")
        script = os.getenv("PLANNING_WORKER_SCRIPT")
        reg = os.path.abspath(os.getenv("PLANNING_REGISTER_DIR", "infra/planning"))
        sdir = os.path.join(os.path.dirname(reg), "scripts", "planning")
        if not script:
            script = os.path.join(sdir, "sheet_worker.py")
        if not py:
            cand = os.path.join(
                sdir,
                ".venv",
                "Scripts" if os.name == "nt" else "bin",
                "python.exe" if os.name == "nt" else "python",
            )
            py = cand if os.path.exists(cand) else "python"
        return [py, "-X", "faulthandler", script]

    def _run_worker(self, job: dict, on_frame, label: str) -> None:
        """Run one capped worker job, handing each output frame to `on_frame`."""
        p = winjob.CappedProcess(
            self._worker_cmd(),
            WORKER_CAP,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        err_tail: collections.deque = collections.deque(maxlen=40)

        def drain():
            for line in p.proc.stderr:
                err_tail.append(line.decode(errors="replace").rstrip())

        th = threading.Thread(target=drain, daemon=True)
        th.start()
        try:
            p.proc.stdin.write(json.dumps(job).encode())
            p.proc.stdin.close()
            rd = p.proc.stdout
            while True:
                head = rd.read(12)
                if len(head) < 12:
                    break
                tag, n = head[:4], struct.unpack("<Q", head[4:])[0]
                on_frame(tag, rd.read(n))
            rc = p.proc.wait()
            th.join(timeout=5)
            peak = p.peak_bytes()
        finally:
            p.close()
        self.stats["worker_runs"] += 1
        self.stats["worker_peak_mb"] = max(
            self.stats["worker_peak_mb"], round(peak / 1e6)
        )
        if rc != 0:
            raise RuntimeError(f"worker exit {rc} for {label}: " + " | ".join(err_tail))

    def _extract(self, row: dict, src: str, work: str) -> SheetData:
        t0 = time.time()
        job = {
            **row,
            "source_path": src,
            "work_dir": work,
            "chunk_m": CHUNK_M,
            "status": (self.plans.get(row["plan_id"]) or {}).get("status"),
        }
        out = {"meta": {}, "zones": [], "overlays": [], "outlines": []}

        def on_frame(tag: bytes, payload: bytes) -> None:
            if tag == b"META":
                out["meta"].update(json.loads(payload))
            elif tag in (b"ZONE", b"OVLY"):
                t = pa.ipc.open_stream(payload).read_all()
                g = shapely.from_wkb(
                    t.column("geometry").to_numpy(zero_copy_only=False)
                )
                bb = tuple(float(v) for v in shapely.total_bounds(g))
                out["zones" if tag == b"ZONE" else "overlays"].append(
                    Chunk(bb, payload, len(g))
                )
            elif tag == b"OUTL":
                out["outlines"].append(shapely.from_wkb(payload))

        self._run_worker(job, on_frame, row["row_id"])
        self.stats["extract_s"][row["row_id"]] = round(time.time() - t0, 1)
        meta = out["meta"]
        sd = SheetData(
            row["row_id"],
            meta.get("labels") or {},
            out["zones"],
            out["overlays"],
            meta=meta,
        )
        outl_meta = meta.get("outlines") or (
            [meta["outline"]] if meta.get("outline") else []
        )
        for i, g in enumerate(out["outlines"]):
            m = outl_meta[i] if i < len(outl_meta) else {}
            shapely.prepare(g)
            sd.outlines.append((m or {}, g))
        if sd.zones:
            sd.zone_tree = shapely.STRtree(
                np.array([shapely.box(*c.bbox) for c in sd.zones], dtype=object)
            )
        if sd.overlays:
            sd.overlay_tree = shapely.STRtree(
                np.array([shapely.box(*c.bbox) for c in sd.overlays], dtype=object)
            )
        sd.nbytes = sum(len(c.data) for c in sd.zones + sd.overlays) + sum(
            _geom_bytes(np.array([g], dtype=object)) for _m, g in sd.outlines
        )
        return sd

    def _store(self, sd: SheetData) -> None:
        if sd.outlines:
            self.cache.pinned.add(sd.row_id)
        self.cache.put(sd.row_id, sd, sd.nbytes)
        # a new sheet can change the merge near it: drop that plan's merged chunks and
        # no-sheet tiles that meet its extent (only those)
        row = self.rows[sd.row_id]
        plan_id, ex = row["plan_id"], row["extent"]["epsg32643"]
        T = UNCOVERED_TILE_M
        for k in list(self.merged.keys()):
            if k[0] != plan_id:
                continue
            if k[1] == "uncovered":
                ix, iy = k[2], k[3]
                hit = _boxes_meet(ex, (ix * T, iy * T, (ix + 1) * T, (iy + 1) * T))
            else:
                r = self.rows.get(k[1])
                hit = r is None or _boxes_meet(ex, r["extent"]["epsg32643"])
            if hit:
                self.merged.pop(k)
        for k in list(self.derived.keys()):
            if k[0] == "f" or k[1] != plan_id:
                continue  # footprints depend on their own sheet only
            if k[0] == "u":
                hit = _boxes_meet(
                    ex, (k[2] * T, k[3] * T, (k[2] + 1) * T, (k[3] + 1) * T)
                )
            else:
                r = self.rows.get(k[2])
                hit = r is None or _boxes_meet(ex, r["extent"]["epsg32643"])
            if hit:
                self.derived.pop(k)

    # ------------------------------------------------------------ outlines
    def outlines(self, plan_id: str) -> list[tuple[dict, shapely.Geometry]] | None:
        """All outlines of a plan's (or the LPA map's) outline rows; None while loading."""
        res = []
        for r in self.outline_rows.get(plan_id, []):
            sd = self.cache.get(r["row_id"])
            if sd is None:
                return None
            res.extend(sd.outlines)
        return res

    def plan_lpa(self, plan_id: str) -> shapely.Geometry | None:
        r = self.outline_row_for(plan_id)
        if r is None:
            return None
        sd = self.cache.get(r["row_id"])
        if sd is None or not sd.outlines:
            return None
        return sd.outlines[0][1]

    def ensure_outlines(self) -> list[dict]:
        rows = [r for rs in self.outline_rows.values() for r in rs]
        return self.ensure(rows)

    # ------------------------------------------------------------ decoding
    def _chunk_gdf(self, row: dict, i: int, overlays: bool = False) -> gpd.GeoDataFrame:
        key = (row["row_id"], i, overlays)
        g = self.decoded.get(key)
        if g is not None:
            return g
        sd = self.cache.get(row["row_id"])
        c = (sd.overlays if overlays else sd.zones)[i]
        t = pa.ipc.open_stream(c.data).read_all()
        geoms = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
        df = t.drop(["geometry"]).to_pandas()
        g = gpd.GeoDataFrame(df, geometry=geoms, crs=CRS_METRIC)
        self.decoded.put(key, g, _geom_bytes(geoms))
        return g

    def raw_pieces(self, row: dict, box: shapely.Geometry) -> gpd.GeoDataFrame:
        """A ready row's pieces whose bbox meets `box`, clipped to its LPA outline when the row
        says so (pieces under half a pixel dropped, as in the original per-sheet step)."""
        sd = self.cache.get(row["row_id"])
        if sd is None or sd.zone_tree is None:
            return gpd.GeoDataFrame({"code": []}, geometry=[], crs=CRS_METRIC)
        parts = []
        for i in sorted(sd.zone_tree.query(box).tolist()):
            g = self._clipped_chunk(row, i)
            if len(g):
                sub = g.iloc[g.sindex.query(box)]
                if len(sub):
                    parts.append(sub)
        if not parts:
            return gpd.GeoDataFrame({"code": []}, geometry=[], crs=CRS_METRIC)
        return gpd.GeoDataFrame(_concat(parts), geometry="geometry", crs=CRS_METRIC)

    def _clipped_chunk(self, row: dict, i: int) -> gpd.GeoDataFrame:
        """One chunk's pieces with chunk / piece ids, clipped to the row's LPA when the row
        says so (cached)."""
        key = (row["row_id"], i, "clipped")
        g = self.decoded.get(key)
        if g is not None:
            return g
        out = self._chunk_gdf(row, i).copy()
        out["chunk"] = i
        out["piece"] = np.arange(len(out))
        clip = row.get("clip") or {}
        if clip.get("to") and len(out):
            lpa = self._clip_geom(row)
            if lpa is None:
                raise _Pending(self.rows[clip["to"]])
            geoms = np.asarray(out.geometry.values)
            inside = shapely.contains(lpa, geoms)
            res_g, res_i = [], []
            min_a = clip.get("min_area_m2", 0.0)
            for k, (g0, ok) in enumerate(zip(geoms, inside, strict=True)):
                gg = g0 if ok else shapely.intersection(g0, lpa)
                for q in shapely.get_parts(gg):
                    if q.geom_type == "Polygon" and q.area >= min_a:
                        res_g.append(q)
                        res_i.append(k)
            out = gpd.GeoDataFrame(
                out.drop(columns="geometry").iloc[res_i].reset_index(drop=True),
                geometry=res_g,
                crs=CRS_METRIC,
            )
        _ = out.sindex
        self.decoded.put(
            key, out, _geom_bytes(np.asarray(out.geometry.values)) if len(out) else 1
        )
        return out

    def _clip_geom(self, row: dict) -> shapely.Geometry | None:
        clip = row.get("clip") or {}
        r = self.rows.get(clip.get("to"))
        if r is None:
            return None
        sd = self.cache.get(r["row_id"])
        if sd is None or not sd.outlines:
            return None
        g = sd.outlines[0][1]
        if clip.get("buffer_m"):  # e.g. Anekal Map 39: its LPA outline + 100 m
            key = ("clip", row["row_id"], "buffer")
            cached = self.decoded.get(key)
            if cached is None:
                cached = g.buffer(clip["buffer_m"])
                shapely.prepare(cached)
                self.decoded.put(
                    key, cached, _geom_bytes(np.array([cached], dtype=object))
                )
            g = cached
        extra = clip.get("also_within")  # e.g. B1: BMRDA pre-STRR extent + 100 m
        if extra:
            ex = self._named_outline(extra["outline"], extra.get("extent"))
            if ex is None:
                return None
            key = ("clip", row["row_id"])
            cached = self.decoded.get(key)
            if cached is None:
                cached = shapely.intersection(g, ex.buffer(extra.get("buffer_m", 0.0)))
                shapely.prepare(cached)
                self.decoded.put(key, cached, 1 << 20)
            g = cached
        return g

    def _named_outline(
        self, authority: str, extent: str | None
    ) -> shapely.Geometry | None:
        for rs in self.outline_rows.values():
            for r in rs:
                sd = self.cache.get(r["row_id"])
                if sd is None:
                    continue
                for m, g in sd.outlines:
                    if m.get("authority") == authority and (
                        extent is None or m.get("extent") == extent
                    ):
                        return g
        return None

    # ------------------------------------------------------------ priority merge
    def plan_rows_in(self, plan_id: str, box: shapely.Geometry) -> list[dict]:
        b = box.bounds
        return [
            r
            for r in self.by_plan.get(plan_id, [])
            if _boxes_meet(r["extent"]["epsg32643"], b)
        ]

    def needed(self, plan_id: str, box: shapely.Geometry) -> list[dict]:
        rows = self.plan_rows_in(plan_id, box)
        need = list(rows)
        for r in rows:
            c = (r.get("clip") or {}).get("to")
            if c and c in self.rows:
                need.append(self.rows[c])
            extra = ((r.get("clip") or {}).get("also_within") or {}).get("row")
            if extra and extra in self.rows:
                need.append(self.rows[extra])
        o = self.outline_row_for(plan_id)
        if o is not None and _boxes_meet(o["extent"]["epsg32643"], box.bounds):
            need.append(o)
        return list({r["row_id"]: r for r in need}.values())

    def _merged_parts(
        self, plan_id: str, box: shapely.Geometry
    ) -> tuple[list[tuple[tuple, gpd.GeoDataFrame]] | None, list[dict]]:
        """(cache key, whole merged frame) of every merged chunk and no-sheet tile of a plan
        that meets `box`, or (None, pending) while a needed sheet is not ready."""
        need = self.needed(plan_id, box)
        if not need:
            return [], []
        pend = self.ensure(need)
        if pend:
            return None, pend
        parts = []
        try:
            self.premerge(plan_id, box, blocking=False)
            for r in self.plan_rows_in(plan_id, box):
                sd = self.cache.get(r["row_id"])
                if sd is None or sd.zone_tree is None:
                    continue
                for i in sorted(sd.zone_tree.query(box).tolist()):
                    m = self._merged_chunk(plan_id, r, i)
                    if len(m):
                        parts.append(((plan_id, r["row_id"], i), m))
            parts.extend(self._uncovered_tiles(plan_id, box))
        except _Pending as p:
            return None, self.ensure([p.row]) or [self.pending_entry(p.row)]
        return parts, []

    def premerge(
        self, plan_id: str, box: shapely.Geometry, blocking: bool = True
    ) -> None:
        """Run the merges `box` still needs (sheet chunks and no-sheet tiles not in the derived
        store) in one capped worker job, so their GEOS work and its transient memory stay out
        of the service. Results go to the derived store, where _merged_chunk and
        _uncovered_tiles find them; if the worker fails they fall back to merging here.
        Call it without holding COMPUTE: the job is set up under COMPUTE, but the worker runs
        without it, so other requests are served meanwhile. One merge job runs at a time."""
        if os.getenv("PLANNING_MERGE_IN_WORKER", "1") != "1":
            return
        if not self._merge_lock.acquire(blocking=blocking):
            return
        try:
            with COMPUTE:
                try:
                    prep = self._premerge_job(plan_id, box)
                except _Pending:
                    return
            if prep is None:
                return
            job, tasks, tiles, work = prep
            try:
                got: list[bytes] = []

                def on_frame(tag: bytes, payload: bytes) -> None:
                    if tag == b"ZONE":
                        got.append(payload)

                t0 = time.time()
                try:
                    self._run_worker(job, on_frame, f"merge {plan_id}")
                except Exception:
                    log.exception(
                        "merge worker failed for %s; merging in the service", plan_id
                    )
                    return
                if len(got) != len(tasks) + len(tiles):
                    log.warning(
                        "merge worker for %s returned %d of %d frames",
                        plan_id,
                        len(got),
                        len(tasks) + len(tiles),
                    )
                    return
                for t, b in zip(tasks, got, strict=False):
                    self.derived.put(("m", *t["key"]), b, len(b))
                for tl, b in zip(tiles, got[len(tasks) :], strict=False):
                    self.derived.put(("u", plan_id, tl["ix"], tl["iy"]), b, len(b))
                self.stats["merge_jobs"] = self.stats.get("merge_jobs", 0) + 1
                self.stats["merge_s"] = round(
                    self.stats.get("merge_s", 0.0) + time.time() - t0, 1
                )
            finally:
                shutil.rmtree(work, ignore_errors=True)
        finally:
            self._merge_lock.release()

    def _premerge_job(self, plan_id: str, box: shapely.Geometry):
        """(job, tasks, tiles, work dir) for the merges `box` still needs, or None."""
        rows = self.by_plan.get(plan_id, [])
        order = {r["row_id"]: k for k, r in enumerate(rows)}
        used: dict[str, set] = {}
        tasks, tiles = [], []

        def ready(rs: list[dict]) -> bool:
            # a chunk / tile whose sheets are still loading is left out of this job (the
            # request reports it pending); the rest are merged now
            return not self.ensure(rs)

        def chunks_in(rs: list[dict], b) -> list[list]:
            res = []
            for rr in rs:
                rsd = self.cache.get(rr["row_id"])
                if rsd is None or rsd.zone_tree is None:
                    continue
                for ci in sorted(rsd.zone_tree.query(b).tolist()):
                    res.append([rr["row_id"], ci])
                    used.setdefault(rr["row_id"], set()).add(ci)
            return res

        for r in self.plan_rows_in(plan_id, box):
            sd = self.cache.get(r["row_id"])
            if sd is None or sd.zone_tree is None:
                continue
            for i in sorted(sd.zone_tree.query(box).tolist()):
                key = (plan_id, r["row_id"], i)
                if key in self.merged or ("m", *key) in self.derived:
                    continue
                cbox = shapely.box(*sd.zones[i].bbox)
                higher = [
                    rr
                    for rr in rows[: order[r["row_id"]]]
                    if _boxes_meet(rr["extent"]["epsg32643"], cbox.bounds)
                ]
                if not ready(higher):
                    continue
                used.setdefault(r["row_id"], set()).add(i)
                tasks.append(
                    {
                        "key": key,
                        "row": r["row_id"],
                        "chunk": i,
                        "higher": chunks_in(higher, cbox),
                        "cut_grid": (r.get("merge") or {}).get("cut_grid"),
                        "min_area_m2": (r.get("clip") or {}).get("min_area_m2", 0.0),
                    }
                )
        cfg = (self.plan_cfg.get(plan_id) or {}).get("uncovered")
        lpa = self.plan_lpa(plan_id) if cfg else None
        if cfg and lpa is not None:
            x0, y0, x1, y1 = box.bounds
            T = UNCOVERED_TILE_M
            for ix in range(math.floor(x0 / T), math.floor(x1 / T) + 1):
                for iy in range(math.floor(y0 / T), math.floor(y1 / T) + 1):
                    if (plan_id, "uncovered", ix, iy) in self.merged or (
                        "u",
                        plan_id,
                        ix,
                        iy,
                    ) in self.derived:
                        continue
                    tile = shapely.box(ix * T, iy * T, (ix + 1) * T, (iy + 1) * T)
                    if not lpa.intersects(tile):
                        continue
                    trs = self.plan_rows_in(plan_id, tile)
                    if not ready(trs):
                        continue
                    tiles.append(
                        {
                            "ix": ix,
                            "iy": iy,
                            "feet": chunks_in(trs, tile),
                            "min_area_m2": cfg.get("min_area_m2", 1.0),
                        }
                    )
        if not tasks and not tiles:
            return None
        work = os.path.join(SVC_TEMP, f"merge_{int(time.time() * 1000)}")
        os.makedirs(work, exist_ok=True)
        try:
            spec = {}
            for k, (rid, cis) in enumerate(used.items()):
                rr = self.rows[rid]
                sd = self.cache.get(rid)
                if sd is None:
                    raise _Pending(rr)
                path = os.path.join(work, f"r{k}.bin")
                with open(path, "wb") as f:
                    for ci in sorted(cis):
                        d = sd.zones[ci].data
                        f.write(struct.pack("<IQ", ci, len(d)))
                        f.write(d)
                ent = {"path": path}
                clip = rr.get("clip") or {}
                if clip.get("to"):
                    g = self._clip_geom(rr)
                    if g is None:
                        raise _Pending(self.rows[clip["to"]])
                    cp = os.path.join(work, f"c{k}.wkb")
                    with open(cp, "wb") as f:
                        f.write(shapely.to_wkb(g))
                    ent["clip_path"] = cp
                    ent["min_area_m2"] = clip.get("min_area_m2", 0.0)
                spec[rid] = ent
            job = {
                "extraction": {"method": "merge_batch"},
                "rows": spec,
                "tasks": [{k: v for k, v in t.items() if k != "key"} for t in tasks],
                "tiles": tiles,
                "tile_m": UNCOVERED_TILE_M,
            }
            if tiles:
                lp = os.path.join(work, "lpa.wkb")
                with open(lp, "wb") as f:
                    f.write(shapely.to_wkb(lpa))
                job["lpa_path"] = lp
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        return job, tasks, tiles, work

    def merged_zones(
        self, plan_id: str, box: shapely.Geometry
    ) -> tuple[gpd.GeoDataFrame | None, list[dict]]:
        """All merged zone pieces of a plan meeting `box` (whole pieces, stable zone_uids), or
        (None, pending) while a needed sheet is not ready."""
        parts, pend = self._merged_parts(plan_id, box)
        if parts is None:
            return None, pend
        frames = [m.iloc[m.sindex.query(box)] for _k, m in parts]
        frames = [f for f in frames if len(f)]
        if not frames:
            return _empty_zones(), []
        out = gpd.GeoDataFrame(_concat(frames), geometry="geometry", crs=CRS_METRIC)
        out = out[~out["zone_uid"].duplicated()].reset_index(drop=True)
        return out, []

    def zone_features_json(
        self, plan_id: str, box: shapely.Geometry, tol: int
    ) -> tuple[list[str] | None, list[dict]]:
        """/zones features as ready GeoJSON text (WGS84, simplified at `tol`, ~0.1 m), from a
        per-chunk display cache; None while a needed sheet is not ready."""
        parts, pend = self._merged_parts(plan_id, box)
        if parts is None:
            return None, pend
        x0, y0, x1, y1 = box.bounds
        out, seen = [], set()
        for key, m in parts:
            bounds, uids, feats = self._display(key, m, tol)
            if not len(feats):
                continue
            hit = (
                (bounds[:, 0] <= x1)
                & (bounds[:, 2] >= x0)
                & (bounds[:, 1] <= y1)
                & (bounds[:, 3] >= y0)
            )
            for j in np.flatnonzero(hit):
                if uids[j] not in seen:
                    seen.add(uids[j])
                    out.append(feats[j])
        return out, []

    def _display(self, key: tuple, m: gpd.GeoDataFrame, tol: int):
        dkey = (*key, "display", tol)
        d = self.merged.get(dkey)
        if d is not None:
            return d
        from app.services.zones_service import COORD_DECIMALS, ZONE_PROPS, plain

        geoms = np.asarray(m.geometry.values)
        if np.isin(shapely.get_type_id(geoms), (3, 6)).all():
            try:
                simp = shapely.coverage_simplify(geoms, tol)
            except shapely.errors.GEOSException:
                simp = shapely.simplify(geoms, tol, preserve_topology=True)
        else:
            simp = shapely.simplify(geoms, tol, preserve_topology=True)
        wgs = gpd.GeoSeries(simp, crs=CRS_METRIC).to_crs(4326).values
        wgs = shapely.transform(
            np.asarray(wgs), lambda xy: np.round(xy, COORD_DECIMALS)
        )
        gj = shapely.to_geojson(wgs)
        props = m.assign(inferred_note=m["note"])
        props["cartographic"] = props["cartographic"].fillna(False).astype(bool)
        recs = props[ZONE_PROPS].to_dict("records")
        feats = [
            '{"type":"Feature","geometry":'
            + g
            + ',"properties":'
            + json.dumps(plain(p))
            + "}"
            for g, p in zip(gj, recs, strict=True)
        ]
        d = (shapely.bounds(geoms), list(m["zone_uid"]), feats)
        self.merged.put(dkey, d, sum(len(f) for f in feats) + 64 * len(feats))
        return d

    def _foot(self, row: dict, ci: int):
        """Union of one (sheet, chunk)'s clipped pieces (cached): its footprint."""
        key = (row["row_id"], ci, "foot")
        f = self.decoded.get(key)
        if f is not None:
            return f if not f.is_empty else None
        packed = self.derived.get(("f", *key[:2]))
        if packed is not None:
            f = _unpack(packed)[0][0]
            shapely.prepare(f)
            self.decoded.put(key, f, _geom_bytes(np.array([f], dtype=object)))
            return f if not f.is_empty else None
        g = self._clipped_chunk(row, ci)
        if not len(g):
            f = shapely.Polygon()
        else:
            geoms = np.asarray(g.geometry.values)
            try:
                f = shapely.coverage_union_all(
                    geoms
                )  # one sheet's pieces do not overlap
            except shapely.errors.GEOSException:
                f = shapely.union_all(geoms, grid_size=0.01)
            f = shapely.make_valid(f)
        b = _pack([f])
        self.derived.put(("f", *key[:2]), b, len(b))
        shapely.prepare(f)
        self.decoded.put(key, f, _geom_bytes(np.array([f], dtype=object)))
        return f if not f.is_empty else None

    def _merged_chunk(self, plan_id: str, row: dict, i: int) -> gpd.GeoDataFrame:
        key = (plan_id, row["row_id"], i)
        m = self.merged.get(key)
        if m is not None:
            return m
        packed = self.derived.get(("m", *key))
        if packed is not None:
            g, cols = _unpack(packed)
            mine = gpd.GeoDataFrame(cols, geometry=list(g), crs=CRS_METRIC)
            m = self._attrs(plan_id, row, mine)
            self.merged.put(key, m, _geom_bytes(g) if len(g) else 1)
            return m
        sd = self.cache.get(row["row_id"])
        cbox = shapely.box(*sd.zones[i].bbox)
        rows = self.by_plan[plan_id]
        k = next(j for j, rr in enumerate(rows) if rr["row_id"] == row["row_id"])
        higher = [
            rr for rr in rows[:k] if _boxes_meet(rr["extent"]["epsg32643"], cbox.bounds)
        ]
        # wait for the higher sheets before any work here (no clipping for a chunk that
        # cannot be merged yet)
        if higher and self.ensure(higher):
            raise _Pending(
                next(rr for rr in higher if self.state[rr["row_id"]].state != "ready")
            )
        mine = self._clipped_chunk(row, i)
        merge = row.get("merge") or {}
        grid = merge.get("cut_grid")
        min_a = (row.get("clip") or {}).get("min_area_m2", 0.0)
        geoms = (
            np.asarray(mine.geometry.values)
            if len(mine)
            else np.array([], dtype=object)
        )
        if higher and len(geoms):
            pend = self.ensure(higher)
            if pend:
                raise _Pending(
                    next(
                        rr for rr in higher if self.state[rr["row_id"]].state != "ready"
                    )
                )
            # one footprint union per higher-priority (sheet, chunk), as the original merge
            # kept one footprint per sheet; each piece is cut by the few that touch it
            ext = shapely.box(*shapely.total_bounds(geoms))
            feet = []
            for rr in higher:
                rsd = self.cache.get(rr["row_id"])
                if rsd is None or rsd.zone_tree is None:
                    continue
                for ci in sorted(rsd.zone_tree.query(ext).tolist()):
                    f = self._foot(rr, ci)
                    if f is not None:
                        feet.append(f)
            per_piece: dict[int, list] = {}
            if feet:
                fa = np.array(feet, dtype=object)
                a_idx, b_idx = shapely.STRtree(geoms).query(fa, predicate="intersects")
                for fj, pj in zip(a_idx.tolist(), b_idx.tolist(), strict=True):
                    per_piece.setdefault(pj, []).append(fa[fj])
            if per_piece:
                new_g, new_i = [], []
                for j, q in enumerate(geoms):
                    hits = per_piece.get(j)
                    if hits:
                        bx = shapely.box(*q.bounds)
                        if grid:
                            cut = [
                                shapely.intersection(c, bx, grid_size=grid)
                                for c in hits
                            ]
                            cut = [
                                p
                                for c in cut
                                for p in shapely.get_parts(c)
                                if p.geom_type == "Polygon"
                            ]
                            if cut:
                                q = shapely.difference(
                                    q,
                                    shapely.union_all(
                                        np.array(cut, dtype=object), grid_size=grid
                                    ),
                                    grid_size=grid,
                                )
                        else:
                            cut = [shapely.intersection(c, bx) for c in hits]
                            q = shapely.difference(q, shapely.union_all(cut))
                    for p in shapely.get_parts(q):
                        if (
                            p.geom_type == "Polygon"
                            and p.area >= min_a
                            and not p.is_empty
                        ):
                            new_g.append(p)
                            new_i.append(j)
                mine = gpd.GeoDataFrame(
                    mine.drop(columns="geometry").iloc[new_i].reset_index(drop=True),
                    geometry=new_g,
                    crs=CRS_METRIC,
                )
        keep = [
            c
            for c in ("code", "note", "cartographic", "chunk", "piece")
            if c in mine.columns
        ]
        b = _pack(np.asarray(mine.geometry.values), {c: list(mine[c]) for c in keep})
        self.derived.put(("m", *key), b, len(b))
        m = self._attrs(plan_id, row, mine)
        self.merged.put(
            key, m, _geom_bytes(np.asarray(m.geometry.values)) if len(m) else 1
        )
        return m

    def _attrs(self, plan_id: str, row: dict, g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        sd = self.cache.get(row["row_id"])
        plan = self.plans.get(plan_id) or {}
        doc = self.docs.get(row["doc_id"]) or {}
        n = len(g)
        codes = [str(int(c)) for c in g["code"]] if n else []
        lab = [sd.labels.get(c, {}) for c in codes]
        qa = row["sheet_qa"]
        pos = row.get("position_uncertainty_m")
        df = {
            "zone_uid": [
                f"{row['row_id']}-{int(c):02d}{int(p):06d}"
                for c, p in zip(g["chunk"], g["piece"], strict=True)
            ]
            if n
            else [],
            "plan_id": [plan_id] * n,
            "doc_id": [row["doc_id"]] * n,
            "zone_label_native": [x.get("zone_label_native") for x in lab],
            "zone_code_native": [None] * n,
            "class_norm": [x.get("class_norm") for x in lab],
            "status": [doc.get("status") or plan.get("status")] * n,
            "status_label": [doc.get("status_label") or plan.get("status_label")] * n,
            "status_condition": [
                (doc.get("status_condition") or plan.get("status_condition")) or None
            ]
            * n,
            "note": list(g["note"]) if "note" in g.columns else [None] * n,
            "cartographic": [bool(x) for x in g["cartographic"]]
            if "cartographic" in g.columns
            else [False] * n,
            "source_layer": [row["source_layer"]] * n,
            "sheet": [row.get("sheet") if row["source_layer"] != "composite" else None]
            * n,
            "position_uncertainty_m": [pos] * n,
            "qa": [qa] * n,
            "row_id": [row["row_id"]] * n,
        }
        # the original zone_uid suffix is per chunk-piece; uid stays stable across requests
        if n and len(set(df["zone_uid"])) != n:
            df["zone_uid"] = [f"{u}-{k}" for k, u in enumerate(df["zone_uid"])]
        return gpd.GeoDataFrame(
            df, geometry=list(g.geometry.values) if n else [], crs=CRS_METRIC
        )

    def _uncovered(
        self, plan_id: str, box: shapely.Geometry
    ) -> gpd.GeoDataFrame | None:
        frames = [
            m.iloc[m.sindex.query(box)] for _k, m in self._uncovered_tiles(plan_id, box)
        ]
        frames = [f for f in frames if len(f)]
        if not frames:
            return None
        return gpd.GeoDataFrame(_concat(frames), geometry="geometry", crs=CRS_METRIC)

    def _uncovered_tiles(self, plan_id: str, box: shapely.Geometry) -> list:
        """(key, frame) per 2 km tile of the plan's LPA area on no sheet, meeting `box`."""
        cfg = (self.plan_cfg.get(plan_id) or {}).get("uncovered")
        if not cfg:
            return []
        lpa = self.plan_lpa(plan_id)
        if lpa is None:
            o = self.outline_row_for(plan_id)
            raise _Pending(o)
        x0, y0, x1, y1 = box.bounds
        T = UNCOVERED_TILE_M
        frames = []
        for ix in range(math.floor(x0 / T), math.floor(x1 / T) + 1):
            for iy in range(math.floor(y0 / T), math.floor(y1 / T) + 1):
                key = (plan_id, "uncovered", ix, iy)
                m = self.merged.get(key)
                packed = self.derived.get(("u", plan_id, ix, iy)) if m is None else None
                if m is None and packed is not None:
                    m = self._uncovered_frame(
                        plan_id, cfg, ix, iy, list(_unpack(packed)[0])
                    )
                    self.merged.put(
                        key,
                        m,
                        _geom_bytes(np.asarray(m.geometry.values)) if len(m) else 1,
                    )
                if m is None:
                    tile = shapely.box(ix * T, iy * T, (ix + 1) * T, (iy + 1) * T)
                    part = shapely.intersection(lpa, tile)
                    geoms = []
                    if not part.is_empty:
                        rows = self.plan_rows_in(plan_id, tile)
                        pend = self.ensure(rows)
                        if pend:
                            raise _Pending(
                                next(
                                    r
                                    for r in rows
                                    if self.state[r["row_id"]].state != "ready"
                                )
                            )
                        fg = []
                        for r in rows:
                            rsd = self.cache.get(r["row_id"])
                            if rsd is None or rsd.zone_tree is None:
                                continue
                            for ci in sorted(rsd.zone_tree.query(tile).tolist()):
                                f = self._foot(r, ci)
                                if f is None or not f.intersects(tile):
                                    continue
                                fg.extend(
                                    q
                                    for q in shapely.get_parts(
                                        shapely.intersection(f, tile)
                                    )
                                    if q.geom_type == "Polygon"
                                )
                        if fg:
                            cut = shapely.union_all(
                                np.array(fg, dtype=object), grid_size=0.01
                            )
                            try:
                                part = shapely.difference(part, cut)
                            except shapely.errors.GEOSException:
                                part = shapely.difference(part, cut, grid_size=0.01)
                        geoms = [
                            p
                            for p in shapely.get_parts(part)
                            if p.geom_type == "Polygon"
                            and p.area >= cfg.get("min_area_m2", 1.0)
                        ]
                    pk = _pack(geoms)
                    self.derived.put(("u", plan_id, ix, iy), pk, len(pk))
                    m = self._uncovered_frame(plan_id, cfg, ix, iy, geoms)
                    self.merged.put(
                        key,
                        m,
                        _geom_bytes(np.array(geoms, dtype=object)) if geoms else 1,
                    )
                if len(m):
                    frames.append((key, m))
        return frames

    def _uncovered_frame(self, plan_id: str, cfg: dict, ix: int, iy: int, geoms: list):
        n = len(geoms)
        plan = self.plans.get(plan_id) or {}
        return gpd.GeoDataFrame(
            {
                "zone_uid": [f"{plan_id}-uncovered-{ix}-{iy}-{k}" for k in range(n)],
                "plan_id": [plan_id] * n,
                "doc_id": [cfg["doc_id"]] * n,
                "zone_label_native": [cfg["zone_label_native"]] * n,
                "zone_code_native": [None] * n,
                "class_norm": [cfg["class_norm"]] * n,
                "status": [plan.get("status")] * n,
                "status_label": [plan.get("status_label")] * n,
                "status_condition": [plan.get("status_condition") or None] * n,
                "note": [cfg["note"]] * n,
                "cartographic": [False] * n,
                "source_layer": [cfg.get("source_layer", "none")] * n,
                "sheet": [None] * n,
                "position_uncertainty_m": [None] * n,
                "qa": [cfg["qa"]] * n,
                "row_id": [None] * n,
            },
            geometry=list(geoms),
            crs=CRS_METRIC,
        )

    # ------------------------------------------------------------ overlays
    def overlays(
        self, plan_id: str, box: shapely.Geometry
    ) -> tuple[gpd.GeoDataFrame | None, list[dict]]:
        rows = [
            r
            for r in self.plan_rows_in(plan_id, box)
            if (r.get("extraction") or {}).get("overlays")
        ]
        if not rows:
            return _empty_overlays(), []
        pend = self.ensure(rows)
        if pend:
            return None, pend
        frames = []
        for r in rows:
            sd = self.cache.get(r["row_id"])
            if sd is None or sd.overlay_tree is None:
                continue
            for i in sorted(sd.overlay_tree.query(box).tolist()):
                g = self._chunk_gdf(r, i, overlays=True)
                sub = g.iloc[g.sindex.query(box)].copy()
                if not len(sub):
                    continue
                doc = self.docs.get(r["doc_id"]) or {}
                plan = self.plans.get(plan_id) or {}
                sub["overlay_uid"] = [
                    f"{r['row_id']}-o{i:03d}-{k:06d}" for k in sub.index
                ]
                sub["plan_id"] = plan_id
                sub["doc_id"] = r["doc_id"]
                sub["class_norm"] = sub["kind"]
                sub["overlay_label_native"] = sub["label"]
                sub["status"] = doc.get("status") or plan.get("status")
                sub["status_label"] = doc.get("status_label") or plan.get(
                    "status_label"
                )
                sub["status_condition"] = (
                    doc.get("status_condition") or plan.get("status_condition")
                ) or None
                sub["qa"] = [r["sheet_qa"]] * len(sub)
                frames.append(sub)
        if not frames:
            return _empty_overlays(), []
        return gpd.GeoDataFrame(
            _concat(frames), geometry="geometry", crs=CRS_METRIC
        ), []

    def _meter(self) -> None:
        """One memory / cache line per minute in the service log (temporary log)."""
        while not self._stop:
            time.sleep(60)
            pa.default_memory_pool().release_unused()
            print(
                "planning: memory " + json.dumps(self.memory()),
                file=sys.stderr,
                flush=True,
            )

    def memory(self) -> dict:
        cur, peak = winjob.process_peak_bytes()
        return {
            "service_mb": round(cur / 1e6),
            "service_peak_mb": round(peak / 1e6),
            "cache_mb": round(self.cache.size / 1e6, 1),
            "decoded_mb": round(self.decoded.size / 1e6, 1),
            "merged_mb": round(self.merged.size / 1e6, 1),
            "derived_mb": round(self.derived.size / 1e6, 1),
            "evicted": self.cache.evicted,
            "worker_peak_mb": self.stats["worker_peak_mb"],
            "worker_runs": self.stats["worker_runs"],
            "temp_peak_mb": round(self.stats["temp_peak_mb"], 1),
            "temp_now_bytes": temp_bytes(),
            "downloads": dict(self.stats["downloads"]),
            "mb_by_host": {
                h: round(b / 1e6, 2) for h, b in self.stats["bytes"].items()
            },
            "first_view_s": self.stats["first_view_s"],
            "extract_s": self.stats["extract_s"],
        }


class _SourceChanged(Exception):
    pass


class _Pending(Exception):
    def __init__(self, row: dict):
        super().__init__(row["row_id"])
        self.row = row


def _pack(geoms, cols: dict | None = None) -> bytes:
    t = pa.table(
        {
            **{c: pa.array(v) for c, v in (cols or {}).items()},
            "geometry": pa.array(
                shapely.to_wkb(np.asarray(geoms, dtype=object)), pa.binary()
            ),
        }
    )
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(
        sink, t.schema, options=pa.ipc.IpcWriteOptions(compression="zstd")
    ) as w:
        w.write_table(t)
    return sink.getvalue().to_pybytes()


def _unpack(b: bytes) -> tuple[np.ndarray, dict]:
    t = pa.ipc.open_stream(b).read_all()
    g = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
    return g, {c: t.column(c).to_pylist() for c in t.column_names if c != "geometry"}


def _boxes_meet(a, b) -> bool:
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def _concat(frames):
    import pandas as pd

    nonempty = [f for f in frames if len(f)]
    return pd.concat(nonempty or frames[:1], ignore_index=True)


ZONE_COLS = [
    "zone_uid",
    "plan_id",
    "doc_id",
    "zone_label_native",
    "zone_code_native",
    "class_norm",
    "status",
    "status_label",
    "status_condition",
    "note",
    "cartographic",
    "source_layer",
    "sheet",
    "position_uncertainty_m",
    "qa",
    "row_id",
]


def _empty_zones() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({c: [] for c in ZONE_COLS}, geometry=[], crs=CRS_METRIC)


def _empty_overlays() -> gpd.GeoDataFrame:
    cols = [
        "overlay_uid",
        "plan_id",
        "doc_id",
        "kind",
        "class_norm",
        "overlay_label_native",
        "status",
        "status_label",
        "status_condition",
        "method",
        "qa",
    ]
    return gpd.GeoDataFrame({c: [] for c in cols}, geometry=[], crs=CRS_METRIC)
