#!/usr/bin/env python3
"""Step A.5 source check before deleting the local raw copies (Tanmay, 2 Oct 2026).

    python -X faulthandler source_check.py --raw C:/Users/tanny/Downloads/planning/raw [--delete]

For every file under --raw: find its register row (infra/planning/plan_docs.csv, by file name
= doc_id), re-download source_url to %TEMP%\\qnit_planning\\check\\, compare sha256 with the
register and with the local file. Only when all three agree are both copies deleted (with
--delete). A file whose URL fails, whose sha256 no longer matches, or that has no register row
is KEPT and listed (path, size, URL, what happened) for Tanmay's call (open-decisions #39).
One download at a time, polite fetching (qnit_fetch). Prints a JSON report; writes nothing else.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# numpy's OpenBLAS reserves a buffer per thread (~800 MB committed on 32 CPUs); nothing here
# needs threaded BLAS, so one thread keeps the 2 GB worker / 1 GB service caps honest
for _v in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_v, "1")

import qnit_fetch as qf

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def sha_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", required=True)
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args()
    with open(
        os.path.join(REPO, "infra", "planning", "plan_docs.csv"), encoding="utf-8"
    ) as f:
        docs = {r["doc_id"]: r for r in csv.DictReader(f)}
    deleted, kept = [], []
    t0 = time.time()
    with qf.PeakMeter() as pm, qf.TempArea("check") as area:
        for root, _d, files in os.walk(args.raw):
            for name in sorted(files):
                path = os.path.join(root, name)
                size = os.path.getsize(path)
                stem = os.path.splitext(name)[0]
                row = docs.get(stem)
                rec = {"path": path, "bytes": size, "doc_id": stem}
                if row is None:
                    kept.append(
                        {**rec, "url": None, "what": "no register row with this doc_id"}
                    )
                    continue
                rec["url"] = row["source_url"]
                local = sha_file(path)
                try:
                    tmp, got = qf.download(
                        row["source_url"], area, "check" + os.path.splitext(name)[1]
                    )
                except Exception as ex:  # noqa: BLE001 - kept and reported
                    kept.append({**rec, "what": f"re-download failed: {str(ex)[:200]}"})
                    continue
                os.remove(tmp)
                if got == row["sha256"] == local:
                    if args.delete:
                        os.remove(path)
                    deleted.append({**rec, "sha256": got})
                else:
                    kept.append(
                        {
                            **rec,
                            "what": f"sha256 differs: source {got[:12]}, register {row['sha256'][:12]}, local {local[:12]}",
                        }
                    )
                print(
                    f"  {len(deleted)} ok, {len(kept)} kept ({time.time() - t0:.0f}s)",
                    flush=True,
                )
    print(
        "SOURCECHECK "
        + json.dumps(
            {
                "deleted" if args.delete else "would_delete": deleted,
                "kept": kept,
                "freed_bytes": sum(d["bytes"] for d in deleted) if args.delete else 0,
                "peak_temp_mb": round(pm.peak / 1e6, 1),
                "fetch": qf.stats_mb(),
                "seconds": round(time.time() - t0),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
