# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Planning service smoke tests — build step 1.3.

Uses the repo's source register (infra/planning) and the synthetic fixture layers in
tests/fixtures/planning (no real plan data). The cadastral call in /zones/at is stubbed.

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

import sys
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


def _make_client(monkeypatch, flags: str):
    monkeypatch.setenv("FLAGS", flags)
    monkeypatch.setenv("PLANNING_REGISTER_DIR", str(_ROOT / "infra" / "planning"))
    monkeypatch.setenv(
        "PLANNING_DATA_DIR", str(_ROOT / "tests" / "fixtures" / "planning")
    )
    monkeypatch.setenv(
        "PLANNING_AUTHORITY_CSV",
        str(_ROOT / "tests" / "fixtures" / "planning" / "authority_villages.csv"),
    )
    for m in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
        sys.modules.pop(m, None)
    from app.auth import verify_token
    from app.main import app

    app.dependency_overrides[verify_token] = lambda: _DUMMY_PAYLOAD
    return TestClient(app), app


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
