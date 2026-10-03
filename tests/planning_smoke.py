# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Planning service smoke tests — build step 1.3 (on-demand layers from 1.18).

Uses the repo's source register (infra/planning) and the synthetic fixture layers in
tests/fixtures/planning (no real plan data), served through a temporary layer index
(file:// sources, test-only) so every test runs the on-demand download / worker / cache
path. The cadastral call in /zones/at is stubbed.

Covers:
  (a) /health
  (b) every gated endpoint -> 403 without flags
  (c) /plans, /docs/{doc_id} (+404)
  (d) /zones: status on every feature, plan flag gate, bbox cap -> 400
  (e) /overlays: each kind, note on every feature, unknown kind -> 400
  (f) /zones/at: overlap, edge distance, position uncertainty, near_edge, inferred,
      overlays_nearby, plans_skipped
  (k) /authority: BDA full / partial / schedule-only village, outside BDA, unknown code,
      other district, point inside / outside the LPA, missing parameters

Run: pytest tests/planning_smoke.py
Requires: cd services/planning && pip install -r requirements.txt
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SVC = _ROOT / "services" / "planning"
if str(_SVC) in sys.path:
    sys.path.remove(str(_SVC))
sys.path.insert(0, str(_SVC))
sys.modules.pop("app", None)

import pytest

try:
    import geopandas  # noqa: F401

    _HAS_GEOPANDAS = True
except ImportError:
    _HAS_GEOPANDAS = False

if not _HAS_GEOPANDAS:
    pytest.skip(
        "geopandas not installed. Run: cd services/planning && pip install -r requirements.txt",
        allow_module_level=True,
    )

from fastapi.testclient import TestClient  # noqa: E402
from pyproj import Transformer  # noqa: E402
from shapely.geometry import box, mapping  # noqa: E402
from shapely.ops import transform  # noqa: E402

_FLAGS = "feature.planning.layers feature.planning.plan.BDA-RMP2031"
_DUMMY_PAYLOAD = {"sub": "test-user", "preferred_username": "smoke-test"}
_E, _N = 780000, 1435000
_TO_WGS = Transformer.from_crs(32643, 4326, always_xy=True).transform


def _wgs_bbox(x0, y0, x1, y1):
    g = transform(_TO_WGS, box(x0, y0, x1, y1))
    return ",".join(f"{v:.6f}" for v in g.bounds)


def _parcel_fc(x0, y0, x1, y1):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": mapping(transform(_TO_WGS, box(x0, y0, x1, y1))),
                "properties": {"survey_no": "1/1/1", "village_name": "Fixture"},
            }
        ],
    }


_FIX = _ROOT / "tests" / "fixtures" / "planning"
# the tests' own temp root: the service's start/stop wipe never reaches a live service's files
_TMP = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp") / "qnit_planning_tests"
os.environ["PLANNING_TEMP_ROOT"] = str(_TMP / "root")


def _fixture_index() -> Path:
    """A layer index whose rows serve the fixture GeoParquets (file:// sources, sha256 checked,
    extraction method fixture_parquet), so the tests run the on-demand path end to end."""
    import pyarrow.parquet as pq

    _TMP.mkdir(parents=True, exist_ok=True)
    (_TMP / ".lock").write_text(str(os.getpid()))
    qa = dict(pq.read_table(_FIX / "BDA-RMP2031.parquet").column("qa")[0].as_py())
    qa.setdefault("source_layer", "composite")
    qa.setdefault("sheet", None)
    qa.setdefault("warnings", [])

    def row(row_id, kind, name, what, **extra):
        p = _FIX / name
        return {
            "row_id": row_id, "kind": kind, "plan_id": "BDA-RMP2031", "authority": "BDA",
            "doc_id": "BDA-RMP2031-PLUCOMP", "page": 1, "sheet": "fixture",
            "sheet_key": what, "source_layer": "composite",
            "source_url": p.as_uri(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "priority": {"rank": 3, "order": [0]},
            "extent": {"epsg32643": [_E - 1000, _N - 1000, _E + 7000, _N + 7000], "wgs84": [77.0, 12.0, 78.0, 13.5]},
            "extraction": {"method": "fixture_parquet", "fixture": what, **extra},
            "sheet_qa": qa, "position_uncertainty_m": None, "placement_confirmed": True,
            "warnings": [], "status": "indexed",
        }

    ix = {
        "build_id": "idx-fixture",
        "plans": {"BDA-RMP2031": {"outline_row": "fix#lpa", "uncovered": None}},
        "rows": [
            row("fix#zones", "zones", "BDA-RMP2031.parquet", "zones"),
            row("fix#overlays", "zones", "BDA-RMP2031_overlays.parquet", "overlays", overlays=True),
            row("fix#lpa", "lpa_outline", "BDA-RMP2031_lpa.parquet", "outline"),
        ],
    }
    path = _TMP / "layer_index.json"
    path.write_text(json.dumps(ix))
    return path


def _make_client(monkeypatch, flags: str):
    monkeypatch.setenv("FLAGS", flags)
    monkeypatch.setenv("PLANNING_REGISTER_DIR", str(_ROOT / "infra" / "planning"))
    monkeypatch.setenv("PLANNING_LAYER_INDEX", str(_fixture_index()))
    monkeypatch.setenv("PLANNING_ALLOW_FILE_SOURCES", "1")
    monkeypatch.setenv("PLANNING_WORKER_PYTHON", sys.executable)
    monkeypatch.setenv(
        "PLANNING_AUTHORITY_CSV",
        str(_FIX / "authority_villages.csv"),
    )
    for m in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
        sys.modules.pop(m, None)
    from app.auth import verify_token
    from app.main import app
    from app.services.store import get_store

    app.dependency_overrides[verify_token] = lambda: _DUMMY_PAYLOAD
    st = get_store()
    t0 = time.time()
    while st.od.ensure(list(st.od.rows.values())):
        assert time.time() - t0 < 120, "fixture sheets did not load"
        time.sleep(0.2)
    return TestClient(app), app


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp():
    yield
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture
def client(monkeypatch):
    c, app = _make_client(monkeypatch, _FLAGS)
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_flags(monkeypatch):
    c, app = _make_client(monkeypatch, "")
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def client_layers_only(monkeypatch):
    c, app = _make_client(monkeypatch, "feature.planning.layers")
    yield c
    app.dependency_overrides.clear()


def _stub_parcel(monkeypatch, fc):
    from app.services import zones_service as zs

    async def fake_fetch(*args, **kwargs):
        return fc

    monkeypatch.setattr(zs, "fetch_parcel", fake_fetch)


def test_a_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "planning"}


def test_b_flag_guard(client_no_flags):
    bbox = _wgs_bbox(_E, _N, _E + 2000, _N + 1000)
    for path in [
        "/plans",
        "/docs/BDA-RMP2031-PLUCOMP",
        "/authority?lat=12.97&lng=77.6",
        f"/zones?plan_id=BDA-RMP2031&bbox={bbox}",
        f"/overlays?plan_id=BDA-RMP2031&bbox={bbox}&kind=ngt_buffer",
        "/zones/at?dist=1&taluk=1&hobli=1&vlg=1&survey=1",
    ]:
        r = client_no_flags.get(path)
        assert r.status_code == 403, f"Expected 403 for {path}, got {r.status_code}"


def test_c_registry(client):
    plans = client.get("/plans").json()
    rmp = next(p for p in plans if p["plan_id"] == "BDA-RMP2031")
    assert rmp["status"] == "draft" and rmp["status_label"] == "Draft, never approved"
    assert rmp["enabled"] is True
    doc = client.get("/docs/BDA-RMP2031-PLUCOMP").json()
    assert doc["status"] == "draft" and len(doc["sha256"]) == 64
    assert client.get("/docs/NOPE").status_code == 404


def test_d_zones(client):
    bbox = _wgs_bbox(_E, _N, _E + 2000, _N + 1000)
    r = client.get(f"/zones?plan_id=BDA-RMP2031&bbox={bbox}")
    assert r.status_code == 200
    fc = r.json()
    assert fc["plan"]["status"] == "draft"
    assert len(fc["features"]) >= 2
    for f in fc["features"]:
        p = f["properties"]
        assert p["status"] == "draft" and p["status_label"] == "Draft, never approved"
        assert p["qa"]["extraction"] == "raster_palette"
    assert (
        client.get("/zones?plan_id=BDA-RMP2031&bbox=77.0,12.0,77.2,12.2").status_code
        == 400
    )
    assert client.get(f"/zones?plan_id=NOPE&bbox={bbox}").status_code == 404


def test_d2_zones_plan_flag_off(client_layers_only):
    bbox = _wgs_bbox(_E, _N, _E + 2000, _N + 1000)
    r = client_layers_only.get(f"/zones?plan_id=BDA-RMP2031&bbox={bbox}")
    assert r.status_code == 403


def test_e_overlays(client):
    boxes = {
        "ngt_buffer": _wgs_bbox(_E - 200, _N - 700, _E + 1200, _N + 100),
        "forest_symbol": _wgs_bbox(_E + 4900, _N + 4900, _E + 6100, _N + 6100),
        "stream_centreline": _wgs_bbox(_E + 900, _N - 200, _E + 1000, _N + 1000),
    }
    for kind, bbox in boxes.items():
        r = client.get(f"/overlays?plan_id=BDA-RMP2031&bbox={bbox}&kind={kind}")
        assert r.status_code == 200, kind
        fc = r.json()
        assert fc["kind"] == kind and len(fc["features"]) == 1
        p = fc["features"][0]["properties"]
        assert p["status"] == "draft" and "not a" in p["note"]
    bbox = boxes["ngt_buffer"]
    assert (
        client.get(f"/overlays?plan_id=BDA-RMP2031&bbox={bbox}&kind=roads").status_code
        == 400
    )


def test_f_zones_at_deep(client, monkeypatch):
    # 60 m square well inside Residential, 20 m from the stream centreline
    _stub_parcel(monkeypatch, _parcel_fc(_E + 400, _N + 400, _E + 460, _N + 460))
    body = client.get("/zones/at?dist=1&taluk=1&hobli=1&vlg=1&survey=1").json()
    (hit,) = body["zones"]
    assert hit["zone_label_native"] == "Residential" and hit["overlap_pct"] == 100.0
    assert hit["position_uncertainty_m"] == pytest.approx(
        (10.1**2 + 4.86**2) ** 0.5, abs=0.1
    )
    assert hit["near_edge"] is False and hit["inferred"] is False
    assert (
        body["overlays_nearby"]["nearest_stream_centreline"] is None
    )  # stream is 490 m away
    assert body["note"] == "Only draft plans cover this parcel; shown for context only"


def test_g_zones_at_edge_and_inferred(client, monkeypatch):
    # straddles Residential / Commercial and the hatch-inferred strip; stream 10 m away
    _stub_parcel(monkeypatch, _parcel_fc(_E + 960, _N - 20, _E + 1040, _N + 40))
    body = client.get("/zones/at?dist=1&taluk=1&hobli=1&vlg=1&survey=1").json()
    labels = {h["zone_label_native"]: h for h in body["zones"]}
    assert set(labels) == {"Residential", "Commercial"}
    assert all(h["near_edge"] for h in body["zones"])
    assert labels["Residential"]["inferred"] is True
    assert labels["Residential"]["inferred_notes"] == ["zone inferred under hatch"]
    assert 0 < labels["Residential"]["inferred_share_pct"] < 100
    near = body["overlays_nearby"]
    assert (
        len(near["ngt_buffer"]) == 1
        and "not a measured buffer" in near["ngt_buffer"][0]["note"]
    )
    assert near["nearest_stream_centreline"]["distance_m"] == pytest.approx(
        10.0, abs=0.5
    )
    assert near["forest_symbol"] == []


def test_h_zones_at_plan_flag_off(client_layers_only, monkeypatch):
    _stub_parcel(monkeypatch, _parcel_fc(_E + 400, _N + 400, _E + 460, _N + 460))
    body = client_layers_only.get(
        "/zones/at?dist=1&taluk=1&hobli=1&vlg=1&survey=1"
    ).json()
    assert body["zones"] == [] and body["plans_skipped"] == ["BDA-RMP2031"]


def test_i_trace_hits(client, monkeypatch):
    # grazes Commercial by 0.4 m (24 m2, 0.4 % of the parcel): a trace hit
    _stub_parcel(monkeypatch, _parcel_fc(_E + 900, _N + 400, _E + 1000.4, _N + 460))
    body = client.get("/zones/at?dist=1&taluk=1&hobli=1&vlg=1&survey=1").json()
    assert [h["zone_label_native"] for h in body["zones"]] == ["Residential"]
    assert [h["zone_label_native"] for h in body["trace_hits"]] == ["Commercial"]
    res = body["zones"][0]
    assert res["near_edge"] is False  # the trace zone's edge does not count
    assert body["trace_hits"][0]["near_edge"] is True  # the trace hit keeps its own


def test_j_simplify_levels(client):
    bbox = _wgs_bbox(_E, _N, _E + 2000, _N + 1000)
    for tol in (2, 8, 25):
        r = client.get(f"/zones?plan_id=BDA-RMP2031&bbox={bbox}&simplify_m={tol}")
        assert r.status_code == 200 and r.json()["simplify_m"] == tol
    assert (
        client.get(f"/zones?plan_id=BDA-RMP2031&bbox={bbox}").json()["simplify_m"] == 8
    )
    assert (
        client.get(f"/zones?plan_id=BDA-RMP2031&bbox={bbox}&simplify_m=5").status_code
        == 400
    )
    r = client.get(
        f"/overlays?plan_id=BDA-RMP2031&bbox={bbox}&kind=stream_centreline&simplify_m=25"
    )
    assert r.status_code == 200 and r.json()["simplify_m"] == 25


def _wgs_point(x, y):
    lng, lat = _TO_WGS(x, y)
    return f"lat={lat:.7f}&lng={lng:.7f}"


def test_k_authority(client):
    full = client.get("/authority?dist=20&taluk=1&hobli=1&vlg=14").json()
    assert full["authority"] == "BDA" and full["coverage"] == "full"
    assert full["operative_plan"] is None
    assert [p["plan_id"] for p in full["draft_plans"]] == ["BDA-RMP2031"]
    assert full["draft_plans"][0]["status"] == "draft"
    assert full["note"] == "Only a draft plan is loaded for this area"
    assert full["share_pct"] == 100.0 and full["pd"] == 8 and full["source"] == "both"

    part = client.get("/authority?dist=20&taluk=1&hobli=1&vlg=11").json()
    assert (
        part["coverage"] == "partial"
        and part["draft_plans"][0]["coverage"] == "partial"
    )

    edge = client.get("/authority?dist=20&taluk=1&hobli=1&vlg=12").json()
    assert edge["authority"] == "BDA" and "text lists it" in edge["mismatch_note"]

    out = client.get("/authority?dist=21&taluk=1&hobli=1&vlg=1").json()
    assert out["authority"] is None and out["coverage"] == "none"
    assert (
        out["draft_plans"] == []
        and out["note"] == "Outside BDA; this area's plan isn't loaded yet"
    )

    assert client.get("/authority?dist=20&taluk=9&hobli=9&vlg=999").status_code == 404
    other = client.get("/authority?dist=5&taluk=1&hobli=1&vlg=1").json()
    assert other["authority"] is None and other["coverage"] == "none"

    inside = client.get(f"/authority?{_wgs_point(_E + 100, _N + 100)}").json()
    assert (
        inside["authority"] == "BDA"
        and inside["coverage"] == "full"
        and inside["source"] == "point"
    )
    outside = client.get(f"/authority?{_wgs_point(_E + 9000, _N + 9000)}").json()
    assert outside["authority"] is None and outside["coverage"] == "none"

    assert client.get("/authority?dist=20&taluk=1").status_code == 400


def test_l_zones_at_parcel_with_spike(client, monkeypatch):
    # a ring with a zero-width spike: make_valid gives Polygon + LineString, whose
    # boundary is None; /zones/at must keep the polygon part, not 500
    x0, y0 = _E + 400, _N + 400
    ring = [
        (x0, y0),
        (x0 + 60, y0),
        (x0 + 60, y0 + 60),
        (x0, y0 + 60),
        (x0, y0 + 30),
        (x0 - 40, y0 + 30),
        (x0, y0 + 30),
        (x0, y0),
    ]
    fc = _parcel_fc(x0, y0, x0 + 60, y0 + 60)
    fc["features"][0]["geometry"] = {
        "type": "Polygon",
        "coordinates": [[list(_TO_WGS(x, y)) for x, y in ring]],
    }
    _stub_parcel(monkeypatch, fc)
    r = client.get("/zones/at?dist=1&taluk=1&hobli=1&vlg=1&survey=1")
    assert r.status_code == 200
    (hit,) = r.json()["zones"]
    assert hit["zone_label_native"] == "Residential" and hit["overlap_pct"] == 100.0


def test_m_road_space_is_cartographic(client, monkeypatch):
    # parcel straddling the uncoloured zone and the 10 m road-space strip: both are hits,
    # road space is flagged cartographic, and the hits add up to 100 %
    _stub_parcel(monkeypatch, _parcel_fc(_E + 2980, _N + 400, _E + 3010, _N + 460))
    body = client.get("/zones/at?dist=1&taluk=1&hobli=1&vlg=1&survey=1").json()
    hits = {z["zone_label_native"]: z for z in body["zones"]}
    road = hits["Road space (not coloured on the plan)"]
    assert road["class_norm"] == "road_space" and road["cartographic"] is True
    assert hits["Not coloured on the plan"]["cartographic"] is False
    assert sum(z["overlap_pct"] for z in body["zones"]) == pytest.approx(100.0, abs=0.1)
    fc = client.get(
        f"/zones?plan_id=BDA-RMP2031&bbox={_wgs_bbox(_E + 2990, _N, _E + 3020, _N + 100)}"
    ).json()
    flags = {
        f["properties"]["class_norm"]: f["properties"]["cartographic"]
        for f in fc["features"]
    }
    assert flags["road_space"] is True and flags["uncoloured"] is False


def test_n_status_condition_everywhere(client, monkeypatch):
    plans = {p["plan_id"]: p for p in client.get("/plans").json()}
    assert "W.P. 4188/2016" in plans["BMRDA-HSK-MP2031"]["status_condition"]
    assert plans["BDA-RMP2031"]["status_condition"] is None
    doc = client.get("/docs/BMRDA-HSK-MP2031-GO-FINALORDER").json()
    assert doc["status"] == "final" and "4188/2016" in doc["status_condition"]
    assert client.get("/docs/BDA-RMP2031-PLUCOMP").json()["status_condition"] is None
    fc = client.get(
        f"/zones?plan_id=BDA-RMP2031&bbox={_wgs_bbox(_E, _N, _E + 500, _N + 500)}"
    ).json()
    assert all("status_condition" in f["properties"] for f in fc["features"])
    assert "status_condition" in fc["plan"]
    _stub_parcel(monkeypatch, _parcel_fc(_E + 400, _N + 400, _E + 460, _N + 460))
    hit = client.get("/zones/at?dist=1&taluk=1&hobli=1&vlg=1&survey=1").json()["zones"][
        0
    ]
    assert "status_condition" in hit and hit["status_condition"] is None


def test_o_edge_distance_to_other_zone_serialises(client, monkeypatch):
    # parcel wholly inside Residential, 100 m from Commercial: the distance comes from an
    # array computation and must serialise as plain JSON numbers / booleans
    _stub_parcel(monkeypatch, _parcel_fc(_E + 850, _N + 400, _E + 900, _N + 450))
    r = client.get("/zones/at?dist=1&taluk=1&hobli=1&vlg=1&survey=1")
    assert r.status_code == 200
    (hit,) = r.json()["zones"]
    assert hit["zone_label_native"] == "Residential"
    assert hit["edge_distance_m"] == pytest.approx(100.0, abs=0.5)
    assert hit["near_edge"] is False


def test_p_contract_1_16(client, monkeypatch):
    # /plans: loaded flag; the fixture loads BDA zones only
    plans = {p["plan_id"]: p for p in client.get("/plans").json()}
    assert plans["BDA-RMP2031"]["loaded"] is True
    assert plans["BIAAPA-MP2021"]["loaded"] is False
    # village inside BDA: one authority entry, its plan loaded, plan refs carry docs
    full = client.get("/authority?dist=20&taluk=1&hobli=1&vlg=14").json()
    assert full["plan_coverage"] == "plan_loaded"
    (entry,) = full["authorities"]
    assert entry["authority"] == "BDA" and entry["plan_coverage"] == "plan_loaded"
    (draft,) = entry["draft_plans"]
    assert draft["loaded"] is True and "BDA-RMP2031-PLUCOMP" in draft["doc_ids"]
    # village outside every LPA: no_master_plan_found, sources checked listed
    out = client.get("/authority?dist=21&taluk=1&hobli=1&vlg=1").json()
    assert out["plan_coverage"] == "no_master_plan_found"
    assert out["authorities"] == [] and len(out["sources_checked"]) >= 3
    assert all("checked_on" in s for s in out["sources_checked"])
    # /zones/at: source layer and sheet on each hit (BDA layer predates 1.16: composite)
    _stub_parcel(monkeypatch, _parcel_fc(_E + 400, _N + 400, _E + 460, _N + 460))
    (hit,) = client.get("/zones/at?dist=1&taluk=1&hobli=1&vlg=1&survey=1").json()[
        "zones"
    ]
    assert hit["source_layer"] == "composite" and hit["mixed_source_layers"] is False
    assert "sheet" in hit and all("source_layer" in q for q in hit["sheets_qa"])
    # 1.17: sheet-level warnings on every SheetQA ([] for layers built before 1.17)
    assert all(q["warnings"] == [] for q in hit["sheets_qa"])


def test_q_unregistered_lpa_is_no_plan_found(client):
    # 1.17: an LPA registered as having no master plan (STRR) has no zone map; an LPA with
    # no register row at all (Magadi) gets no_master_plan_found
    client.get("/plans")  # store loaded
    from app.routers.registry import _plan_coverage

    assert _plan_coverage("STRR", []) == "lpa_no_zone_map"
    assert _plan_coverage("MAGADI", []) == "no_master_plan_found"
    assert _plan_coverage("BIAAPA", ["BIAAPA-MP2021"]) == "plan_registered_not_loaded"


def test_r_merges_run_in_the_worker(client, monkeypatch):
    # #46: the priority merge runs in the capped worker; the service only keeps the results,
    # and the answer matches the in-service merge
    from app.services.store import get_store

    _stub_parcel(monkeypatch, _parcel_fc(_E + 400, _N + 400, _E + 460, _N + 460))
    od = get_store().od
    jobs0 = od.stats.get("merge_jobs", 0)
    a = client.get("/zones/at?dist=1&taluk=1&hobli=1&vlg=1&survey=1").json()
    assert od.stats.get("merge_jobs", 0) > jobs0
    for k in [k for k in od.derived.keys() if k[0] in ("m", "u")]:
        od.derived.pop(k)
    for k in list(od.merged.keys()):
        od.merged.pop(k)
    monkeypatch.setenv("PLANNING_MERGE_IN_WORKER", "0")
    b = client.get("/zones/at?dist=1&taluk=1&hobli=1&vlg=1&survey=1").json()
    assert a["zones"] == b["zones"] and a["trace_hits"] == b["trace_hits"]
