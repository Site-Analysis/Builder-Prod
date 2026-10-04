"""Georeference a raster plan sheet that prints no coordinates, from OSM roads.

Used for the Nelamangala Master Plan 2031 grids, which carry no grid labels or graticule.
North-up and the printed scale are assumed for the start; the result is an affine
px -> EPSG:32643 (shapely order a, b, d, e, xoff, yoff) and its own checks.

  1. coarse: zero-mean FFT cross-correlation of the sheet's road mask (red road lines +
     the TRANSPORTATION fill) with OSM MAJOR roads (trunk-tertiary; minor roads make the
     score flat) over the search box, 8 m cells; peak ratio = peak / best peak > 320 m away
  2. fine: the same on 2 m cells, all OSM roads, within +-800 m of the coarse position
  3. junction affine: sheet road junctions vs OSM junctions of the same degree within
     20 m then 10 m; least squares on the even-numbered matches only
  4. checks (both must pass to accept the georeference):
     - held out: the odd-numbered matches (never used for the fit), RMSE at 10 m;
     - null baseline: the same junction match at 8 shifted placements (1.5-3.5 km away):
       matches must be >= 3x the null mean. Validated on Hoskote sheets with a known grid:
       a wrong placement 13 km away still scored 21 held-out matches at 5.8 m RMSE, which
       is why the held-out RMSE alone is not accepted.
"""

import numpy as np
import shapely
from extract_hoskote import raster_junctions
from extract_plucomp import dilate

COARSE_M, FINE_M, FINE_WIN_M = 8.0, 2.0, 800.0
MAJOR = {
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "motorway_link",
    "trunk_link",
    "primary_link",
    "secondary_link",
    "tertiary_link",
}
NULL_SHIFTS_M = [
    (1500, 0),
    (-1500, 0),
    (0, 1500),
    (0, -1500),
    (2500, 2500),
    (-2500, 2500),
    (2500, -2500),
    (-3000, -1000),
]


def road_mask(a, transport_mask=None):
    """Red road lines (thin red, not the red PSP fill) plus the transport class."""
    r, g, b = (a[..., i].astype(np.int16) for i in range(3))
    red = (r > 170) & (g < 140) & (b < 140)
    fill = dilate(
        ~dilate(~red, 2), 2
    )  # opening: red areas wider than ~5 px are PSP fills
    lines = red & ~dilate(fill, 1)
    if transport_mask is not None:
        lines |= transport_mask
    return lines


def major(elements):
    return [e for e in elements if (e.get("tags") or {}).get("highway") in MAJOR]


def rasterise_lines(elements, tr, box, cell):
    """OSM ways (with geometry) -> boolean raster over box (x0, y0, x1, y1), row 0 = north."""
    x0, y0, x1, y1 = box
    W, H = int((x1 - x0) / cell) + 1, int((y1 - y0) / cell) + 1
    R = np.zeros((H, W), bool)
    for e in elements:
        geom = e.get("geometry") or []
        if len(geom) < 2:
            continue
        xs, ys = tr.transform([q["lon"] for q in geom], [q["lat"] for q in geom])
        xs, ys = np.asarray(xs), np.asarray(ys)
        if xs.max() < x0 or xs.min() > x1 or ys.max() < y0 or ys.min() > y1:
            continue
        for i in range(len(xs) - 1):
            n = (
                int(max(abs(xs[i + 1] - xs[i]), abs(ys[i + 1] - ys[i])) / (cell / 2))
                + 2
            )
            c = ((np.linspace(xs[i], xs[i + 1], n) - x0) / cell).astype(int)
            r_ = ((y1 - np.linspace(ys[i], ys[i + 1], n)) / cell).astype(int)
            ok = (c >= 0) & (c < W) & (r_ >= 0) & (r_ < H)
            R[r_[ok], c[ok]] = True
    return R


def downsample(mask, f):
    H, W = (mask.shape[0] // f) * f, (mask.shape[1] // f) * f
    return mask[:H, :W].reshape(H // f, f, W // f, f).any(axis=(1, 3))


def _xcorr(O, S):
    """Zero-mean cross-correlation; C[iy, ix] = score with S's (0,0) at O's (iy, ix)."""
    O = dilate(O, 1).astype(np.float32)
    S = dilate(S, 1).astype(np.float32)
    O -= O.mean()
    S -= S.mean()
    Hf = 1 << (O.shape[0] + S.shape[0] - 1).bit_length()
    Wf = 1 << (O.shape[1] + S.shape[1] - 1).bit_length()
    C = np.fft.irfft2(
        np.fft.rfft2(O, (Hf, Wf)) * np.conj(np.fft.rfft2(S, (Hf, Wf))), (Hf, Wf)
    )
    return C[: O.shape[0], : O.shape[1]]


def _peak(C, excl):
    iy, ix = np.unravel_index(int(np.argmax(C)), C.shape)
    C2 = C.copy()
    C2[max(0, iy - excl) : iy + excl, max(0, ix - excl) : ix + excl] = C.min()
    top2 = float(C2.max())
    ratio = float(C[iy, ix] / top2) if top2 > 0 else float("inf")
    return int(iy), int(ix), ratio


def place(sheet_roads, m_px, osm_major_raster, osm_all, tr, box):
    """Translation of the north-up sheet (coarse then fine). Returns (affine, info)."""
    x0, _y0, _x1, y1 = box
    f = max(1, round(COARSE_M / m_px))
    iy, ix, ratio_c = _peak(
        _xcorr(osm_major_raster, downsample(sheet_roads, f)), int(320 / COARSE_M)
    )
    e0, n0 = x0 + ix * COARSE_M, y1 - iy * COARSE_M
    Hs, Ws = sheet_roads.shape
    fb = (
        e0 - FINE_WIN_M,
        n0 - Hs * m_px - FINE_WIN_M,
        e0 + Ws * m_px + FINE_WIN_M,
        n0 + FINE_WIN_M,
    )
    O = rasterise_lines(osm_all, tr, fb, FINE_M)
    g = max(1, round(FINE_M / m_px))
    jy, jx, ratio_f = _peak(_xcorr(O, downsample(sheet_roads, g)), int(40 / FINE_M))
    e1, n1 = fb[0] + jx * FINE_M, fb[3] - jy * FINE_M
    A = [m_px, 0.0, 0.0, -m_px, e1, n1]
    return A, {
        "coarse_peak_ratio": round(ratio_c, 2),
        "fine_peak_ratio": round(ratio_f, 2),
        "coarse_to_fine_m": round(float(np.hypot(e1 - e0, n1 - n0)), 1),
    }


def apply(A, xy):
    return np.column_stack(
        [
            A[0] * xy[:, 0] + A[1] * xy[:, 1] + A[4],
            A[2] * xy[:, 0] + A[3] * xy[:, 1] + A[5],
        ]
    )


def _match(G, pdeg, osm_xy, osm_deg, tree, radius):
    idx = tree.query(shapely.points(G), predicate="dwithin", distance=radius)
    best = {}
    for a, b in zip(*idx, strict=True):
        if pdeg[a] != osm_deg[b]:
            continue
        d = float(np.hypot(*(G[a] - osm_xy[b])))
        if a not in best or d < best[a][0]:
            best[a] = (d, b)
    return best


def refine(sheet_roads, m_px, A, osm_xy, osm_deg, osm_tree):
    """Junction affine (fit on even matches), held-out check (odd), null baseline."""
    pj, pdeg = raster_junctions(sheet_roads, m_px, lambda xy: xy)
    if len(pj) == 0:
        return A, {
            "sheet_junctions": 0,
            "matched": 0,
            "accepted": False,
            "flag": "no sheet junctions",
        }
    A = np.array(A, float)
    for radius in (20.0, 10.0, 10.0):
        best = _match(apply(A, pj), pdeg, osm_xy, osm_deg, osm_tree, radius)
        if len(best) < 12:
            break
        fit = sorted(best)[0::2]
        M = np.column_stack([pj[fit], np.ones(len(fit))])
        dst = osm_xy[[best[k][1] for k in fit]]
        cx, *_ = np.linalg.lstsq(M, dst[:, 0], rcond=None)
        cy, *_ = np.linalg.lstsq(M, dst[:, 1], rcond=None)
        A = np.array([cx[0], cx[1], cy[0], cy[1], cx[2], cy[2]])
    best = _match(apply(A, pj), pdeg, osm_xy, osm_deg, osm_tree, 10.0)
    ks = sorted(best)
    held = [best[k][0] for k in ks[1::2]]
    fitd = [best[k][0] for k in ks[0::2]]
    null = []
    for dx, dy in NULL_SHIFTS_M:
        B = A.copy()
        B[4] += dx
        B[5] += dy
        null.append(len(_match(apply(B, pj), pdeg, osm_xy, osm_deg, osm_tree, 10.0)))
    null_mean = float(np.mean(null))
    rmse = float(np.sqrt(np.mean(np.square(held)))) if held else None
    ok_null = len(best) >= 3 * max(null_mean, 1.0)
    ok_rmse = rmse is not None and rmse <= 10.0 and len(held) >= 3
    flags = []
    if not ok_null:
        flags.append(
            f"matches {len(best)} < 3x null baseline {null_mean:.1f}: placement not confirmed"
        )
    if not ok_rmse:
        flags.append("held-out RMSE > 10 m or fewer than 3 held-out matches")
    out = {
        "sheet_junctions": len(pj),
        "matched": len(held),
        "matched_fit": len(fitd),
        "matched_total": len(best),
        "null_matches_mean": round(null_mean, 1),
        "rmse_m": rmse,
        "fit_rmse_m": float(np.sqrt(np.mean(np.square(fitd)))) if fitd else None,
        "m_per_px_fitted": float(np.sqrt(abs(A[0] * A[3] - A[1] * A[2]))),
        "rotation_deg": float(np.degrees(np.arctan2(A[2], A[0]))),
        "accepted": ok_null and ok_rmse,
        "flag": "; ".join(flags),
        "check": "held-out half of the junction matches (never used for the fit) + null baseline",
    }
    return [float(v) for v in A], out
