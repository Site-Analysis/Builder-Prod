#!/usr/bin/env python3
"""Extract one indexed sheet (or one LPA outline) in a subprocess for the planning service.

    python -X faulthandler sheet_worker.py < job.json > frames

The job (JSON on stdin) is one layer_index.json row plus:
    source_path  the downloaded source in %TEMP%\\qnit_planning (deleted by the service)
    work_dir     an empty scratch folder in %TEMP%\\qnit_planning (deleted by the service)
    chunk_m      side of the square chunks the vectors are grouped into (default 2000 m)

It reuses the per-sheet code of the original extraction scripts with the index's stored
calibration (no OSM, no network) and writes length-prefixed frames to stdout:
    b"META" json   sheet summary (labels by code, areas, seconds)
    b"ZONE" ipc    one chunk of zone pieces (Arrow IPC, zstd): code, note, cartographic, geometry (WKB, EPSG:32643)
    b"OVLY" ipc    one chunk of overlay features: kind, label, method, geometry
    b"OUTL" wkb    an LPA outline (EPSG:32643), with its authority / extent in META
    b"DONE" json   end marker
Raster-heavy steps of the composite run in row bands with margins wider than every
neighbourhood operation, so the output is the same as the one-pass scripts (memory only).
"""

from __future__ import annotations

import json
import os
import struct
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

import numpy as np
import pyarrow as pa
import shapely

OUT = sys.stdout.buffer


def _mem(msg: str, t0: float) -> None:
    from winjob import process_peak_bytes

    cur, peak = process_peak_bytes()
    print(
        f"{msg}: {cur / 1e6:.0f} MB now, {peak / 1e6:.0f} MB peak ({time.time() - t0:.0f}s)",
        file=sys.stderr,
        flush=True,
    )


def frame(tag: bytes, payload: bytes) -> None:
    OUT.write(tag + struct.pack("<Q", len(payload)) + payload)
    OUT.flush()


def frame_json(tag: bytes, obj) -> None:
    frame(tag, json.dumps(obj).encode())


def ipc(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(
        sink, table.schema, options=pa.ipc.IpcWriteOptions(compression="zstd")
    ) as w:
        w.write_table(table)
    return sink.getvalue().to_pybytes()


def emit_chunks(tag: bytes, geoms: list, cols: dict[str, list], chunk_m: float) -> int:
    """Group features by the chunk holding their bbox centre; one frame per chunk."""
    if not geoms:
        return 0
    G = np.array(geoms, dtype=object)
    b = shapely.bounds(G)
    cx = np.floor(((b[:, 0] + b[:, 2]) / 2) / chunk_m).astype(np.int64)
    cy = np.floor(((b[:, 1] + b[:, 3]) / 2) / chunk_m).astype(np.int64)
    key = cx * 1_000_000 + cy
    order = np.argsort(key, kind="stable")
    n = 0
    for k in np.unique(key):
        idx = order[key[order] == k]
        t = pa.table(
            {
                **{c: pa.array([v[i] for i in idx]) for c, v in cols.items()},
                "geometry": pa.array(shapely.to_wkb(G[idx]), pa.binary()),
            }
        )
        frame(tag, ipc(t))
        n += 1
    return n


# ---------------------------------------------------------------- BDA PLUCOMP composite
BAND = 1024  # core rows per band (a multiple of the 256-px polygonise tile)
MARGIN = (
    640  # >= every neighbourhood op (forest close 30 + dilate 2 -> 62 px) + fill reach
)


def _fill_count(cls, unknown, not_source=None):
    """extract_plucomp.fill_from_neighbours with the number of passes returned (same logic)."""
    unknown = unknown.copy()
    blocked = not_source if not_source is not None else np.zeros_like(unknown)
    passes = 0
    while unknown.any():
        before = unknown.sum()
        passes += 1
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            src_y = slice(max(-dy, 0), cls.shape[0] - max(dy, 0))
            dst_y = slice(max(dy, 0), cls.shape[0] - max(-dy, 0))
            src_x = slice(max(-dx, 0), cls.shape[1] - max(dx, 0))
            dst_x = slice(max(dx, 0), cls.shape[1] - max(-dx, 0))
            take = (
                unknown[dst_y, dst_x] & ~unknown[src_y, src_x] & ~blocked[src_y, src_x]
            )
            cls[dst_y, dst_x][take] = cls[src_y, src_x][take]
            unknown[dst_y, dst_x][take] = False
        if unknown.sum() == before:
            break
    if unknown.any() and not_source is not None:
        cls, p2 = _fill_count(cls, unknown)
        passes += p2
    return cls, passes


def plucomp(job: dict) -> None:
    import extract_plucomp as xp
    import pymupdf

    t0 = time.time()
    A = np.array(job["georef"]["affine_page_to_32643"])
    doc = pymupdf.open(job["source_path"])
    page = doc[(job.get("page") or 1) - 1]
    colour_code, zones, info = xp.load_legend()
    strips = sorted(
        [x for x in page.get_image_info(xrefs=True) if x["width"] == xp.STRIP_W],
        key=lambda x: x["bbox"][1],
    )
    pymupdf.TOOLS.store_shrink(100)  # image_info decodes the strips into MuPDF's store
    x0, y0 = strips[0]["bbox"][0], strips[0]["bbox"][1]
    cpt = (strips[0]["bbox"][2] - x0) / xp.STRIP_W
    rpt = xp.ROW_PT
    H, W = round((strips[-1]["bbox"][3] - y0) / rpt), xp.STRIP_W
    grid = (x0, y0, cpt, rpt)
    main = {xp.rgb_int(h): c for h, c in colour_code.items()}
    main[0xFFFFFF] = xp.UNCOLOURED
    keys = np.array(sorted(main), dtype=np.uint32)
    codes = np.array([main[k] for k in keys], dtype=np.uint8)
    krgb = np.stack([(keys >> 16) & 255, (keys >> 8) & 255, keys & 255], 1).astype(int)
    lut_cache: dict[int, int] = {}
    lpa_page = xp.lpa_polygon_page(page)

    code_r = np.zeros((H, W), np.uint8)
    ngt_bits = np.zeros((H, (W + 7) // 8), np.uint8)
    forest_bits = np.zeros_like(ngt_bits)
    stream_bits = np.zeros_like(ngt_bits)
    stats = {"blend_px": 0, "inside_px": 0, "inf_hatch_px": 0, "inf_stream_px": 0}
    max_pass = 0
    for r0 in range(0, H, BAND):
        r1 = min(H, r0 + BAND)
        w0, w1 = max(0, r0 - MARGIN), min(H, r1 + MARGIN)
        img = np.zeros((w1 - w0, W), np.uint32)
        for s in strips:
            sr0 = round((s["bbox"][1] - y0) / rpt)
            if sr0 + s["height"] <= w0 or sr0 >= w1:
                continue
            pix = pymupdf.Pixmap(doc, s["xref"])
            a = np.frombuffer(pix.samples, np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            if s["transform"][3] < 0:
                a = a[::-1]
            v = (
                (a[..., 0].astype(np.uint32) << 16)
                | (a[..., 1].astype(np.uint32) << 8)
                | a[..., 2]
            )
            v = v[: H - sr0]
            lo, hi = max(sr0, w0), min(sr0 + len(v), w1)
            img[lo - w0 : hi - w0] = v[lo - sr0 : hi - sr0]
            del pix, a, v
        if r0 == 0:
            _mem("band 0 raster loaded", t0)
        # palette lookup per distinct colour (same rule as the script), then pixels mapped in
        # row chunks: no full-size int64 inverse index (memory)
        uniq = np.unique(img)
        lut = np.zeros(len(uniq), np.uint8)
        exact = np.zeros(len(uniq), bool)
        for i, u in enumerate(uniq.tolist()):
            if u not in lut_cache:
                j = np.searchsorted(keys, u)
                if j < len(keys) and keys[j] == u:
                    lut_cache[u] = int(codes[j])
                else:
                    rgb = np.array([(u >> 16) & 255, (u >> 8) & 255, u & 255])
                    lut_cache[u] = int(codes[np.argmin(((krgb - rgb) ** 2).sum(1))])
            lut[i] = lut_cache[u]
            j = np.searchsorted(keys, u)
            exact[i] = j < len(keys) and keys[j] == u
        cls = np.empty(img.shape, np.uint8)
        for a0 in range(0, img.shape[0], 256):
            idx = np.searchsorted(uniq, img[a0 : a0 + 256])
            cls[a0 : a0 + 256] = lut[idx]
            lo_c, hi_c = max(a0, r0 - w0), min(a0 + 256, r1 - w0)
            if hi_c > lo_c:
                stats["blend_px"] += int((~exact[idx[lo_c - a0 : hi_c - a0]]).sum())
            del idx
        del img, uniq
        if r0 == 0:
            _mem("band 0 classified", t0)
        # LPA mask for the window rows (same raster grid as the full-page render)
        inside = xp.render_mask(
            page,
            lpa_page,
            (x0, y0 + w0 * rpt, cpt, rpt),
            cls.shape,
        )
        hatch = cls == xp.HATCH
        ngt = xp.close(hatch, xp.HATCH_CLOSE_PX) & inside
        teal = cls == zones["Streams"]
        light = cls == zones["Water Bodies"]
        casing = (
            light
            & ~xp.opening(light, xp.STREAM_CASING_PX)
            & xp.dilate(teal, xp.STREAM_REACH_PX)
        )
        stream_sym = (teal | casing) & inside
        lakes = light & ~casing
        glyph = cls == xp.GLYPH
        forest_area = (
            xp.dilate(xp.close(glyph, xp.FOREST_CLOSE_PX), xp.FOREST_DILATE_PX) & inside
        )
        del teal, light, casing
        if r0 == 0:
            _mem("band 0 symbol masks", t0)
        cls, passes = _fill_count(cls, hatch | stream_sym | glyph, not_source=lakes)
        max_pass = max(max_pass, passes)
        inf_hatch = ngt & ~lakes & ~stream_sym
        del lakes, glyph, hatch
        cls[~inside] = xp.BACKGROUND
        zone_px = (cls >= 10) | (cls == xp.UNCOLOURED)
        cr = cls.copy()
        cr[stream_sym & zone_px] |= xp.STREAM_BIT
        cr[inf_hatch & zone_px] |= xp.HATCH_BIT
        c0, c1 = r0 - w0, r1 - w0
        code_r[r0:r1] = cr[c0:c1]
        ngt_bits[r0:r1] = np.packbits(ngt[c0:c1], axis=1)
        forest_bits[r0:r1] = np.packbits(forest_area[c0:c1], axis=1)
        stream_bits[r0:r1] = np.packbits(stream_sym[c0:c1], axis=1)
        stats["inside_px"] += int(inside[c0:c1].sum())
        stats["inf_hatch_px"] += int(inf_hatch[c0:c1].sum())
        stats["inf_stream_px"] += int(stream_sym[c0:c1].sum())
        del cls, cr, inside, ngt, stream_sym, forest_area, inf_hatch, zone_px
        _mem(f"band {r0}-{r1} fill passes {passes}", t0)
    if max_pass >= MARGIN - 70:
        raise SystemExit(f"fill reach {max_pass} px exceeds the band margin {MARGIN}")

    # stream centrelines: the same tiled Zhang-Suen over the whole mask as the script
    stream = np.unpackbits(stream_bits, axis=1, count=W).astype(bool)
    del stream_bits
    skel = xp.skeleton_tiled(stream)
    del stream
    stream_lines = xp.skeleton_lines(skel)
    del skel
    _mem("stream centrelines", t0)

    M = np.array([[cpt, 0], [0, rpt]]) @ A[:2]
    off = np.array([x0, y0]) @ A[:2] + A[2]

    def to_ground(g):
        return shapely.transform(g, lambda xy: xy @ M + off)

    lpa_g = shapely.make_valid(
        to_ground(shapely.transform(lpa_page, lambda xy: (xy - [x0, y0]) / [cpt, rpt]))
    )
    px_area = abs(np.linalg.det(A[:2])) * cpt * rpt
    names = {v: k for k, v in zones.items()}
    names[xp.UNCOLOURED] = info[xp.UNCOLOURED]["zone_label_native"]
    names[xp.ROAD_SPACE] = xp.ROAD_SPACE_LABEL
    info[xp.ROAD_SPACE] = next(
        r
        for r in xp.read_csv(xp.LEGEND_CSV)
        if r["zone_label_native"] == xp.ROAD_SPACE_LABEL
    )
    shapely.prepare(lpa_g)
    seen_codes: set[int] = set()
    area_by: dict[str, float] = {}
    counts = {"zones": 0, "chunks": 0}

    def note_of(code_f):
        flags = {
            f
            for f, bit in (("stream", xp.STREAM_BIT), ("hatch", xp.HATCH_BIT))
            if code_f & bit
        }
        return (
            "; ".join(
                n
                for f, n in (
                    ("stream", "zone inferred under stream symbol"),
                    ("hatch", "zone inferred under hatch"),
                )
                if f in flags
            )
            or None
        )

    def finish(done: dict) -> None:
        """Finished components (value -> parts): the script's per-part rules (thin white
        -> road space, ground transform, LPA clip, half-pixel filter), then emitted."""
        geoms, c_code, c_note, c_carto = [], [], [], []
        for code_f, parts_all in done.items():
            code = code_f & (xp.STREAM_BIT - 1)
            if code in (xp.BACKGROUND, xp.HATCH, xp.GLYPH):
                continue
            note = note_of(code_f)
            for b0 in range(0, len(parts_all), 5000):
                parts = np.array(parts_all[b0 : b0 + 5000], dtype=object)
                thin = shapely.is_empty(
                    shapely.buffer(parts, -xp.SLIVER_PX / 2, join_style="mitre")
                )
                for p, is_thin in zip(parts, thin, strict=True):
                    out_code = (
                        xp.ROAD_SPACE if (is_thin and code == xp.UNCOLOURED) else code
                    )
                    gp = to_ground(p)
                    if not lpa_g.contains(gp):
                        gp = shapely.intersection(gp, lpa_g)
                    if gp.is_empty:
                        continue
                    for q in shapely.get_parts(gp):
                        if q.geom_type != "Polygon" or q.area < 0.5 * px_area:
                            continue
                        geoms.append(q)
                        c_code.append(int(out_code))
                        c_note.append(note)
                        c_carto.append(out_code == xp.ROAD_SPACE)
                del parts, thin
        for c, q in zip(c_code, geoms, strict=True):
            seen_codes.add(c)
            k = info[c]["class_norm"] or names[c]
            area_by[k] = area_by.get(k, 0.0) + q.area
        counts["zones"] += len(geoms)
        counts["chunks"] += emit_chunks(
            b"ZONE",
            geoms,
            {"code": c_code, "note": c_note, "cartographic": c_carto},
            job.get("chunk_m", 2000.0),
        )

    # polygonise band by band; a component is finished once it no longer reaches the next
    # band seam, so only the components open across a seam stay in memory
    _stream_polygonise(code_r, finish)
    del code_r
    _mem(f"{counts['zones']} zone pieces emitted", t0)
    labels = {
        str(c): {
            "zone_label_native": names[c],
            "class_norm": info[c]["class_norm"] or None,
        }
        for c in seen_codes
    }
    n_z, nz = counts["chunks"], counts["zones"]
    ngt_geom = _as_geom(
        _polygonise_banded(
            np.unpackbits(ngt_bits, axis=1, count=W).astype(np.uint8)
        ).get(1)
    )
    del ngt_bits
    forest_geom = _as_geom(
        _polygonise_banded(
            np.unpackbits(forest_bits, axis=1, count=W).astype(np.uint8)
        ).get(1)
    )
    del forest_bits

    ngt_g = (
        shapely.make_valid(to_ground(ngt_geom)).buffer(0)
        if ngt_geom is not None
        else None
    )
    forest_g = (
        shapely.make_valid(to_ground(forest_geom)).buffer(0)
        if forest_geom is not None
        else None
    )
    stream_g = [
        q
        for q in shapely.get_parts(shapely.intersection(to_ground(stream_lines), lpa_g))
        if q.geom_type == "LineString"
    ]

    def polys_in_lpa(g):
        if g is None:
            return []
        return [
            q
            for q in shapely.get_parts(g.intersection(lpa_g))
            if q.geom_type == "Polygon"
        ]

    ov_g, ov_kind, ov_label, ov_method = [], [], [], []
    forest_method = (
        f"overlay: glyph pixels closed {xp.FOREST_CLOSE_PX}px then dilated "
        f"{xp.FOREST_DILATE_PX}px; zone = ground colour"
    )
    stream_method = (
        "overlay: teal core + thin light-blue casing, Zhang-Suen skeleton; "
        "zone under symbol filled from land"
    )
    for g_list, kind, label, method in (
        (
            polys_in_lpa(ngt_g),
            "ngt_buffer",
            "NGT Buffer",
            f"hatch pixels closed {xp.HATCH_CLOSE_PX}px",
        ),
        (polys_in_lpa(forest_g), "forest_symbol_area", "Forest", forest_method),
        (stream_g, "stream_centreline", "Streams", stream_method),
    ):
        for q in g_list:
            ov_g.append(q)
            ov_kind.append(kind)
            ov_label.append(label)
            ov_method.append(method)
    n_o = emit_chunks(
        b"OVLY",
        ov_g,
        {"kind": ov_kind, "label": ov_label, "method": ov_method},
        job.get("chunk_m", 2000.0),
    )
    frame(b"OUTL", shapely.to_wkb(lpa_g))
    frame_json(
        b"META",
        {
            "labels": labels,
            "zones": nz,
            "zone_chunks": n_z,
            "overlays": len(ov_g),
            "overlay_chunks": n_o,
            "outline": {"authority": job.get("authority"), "extent": "plan"},
            "area_ha_by_class": {
                k: round(v / 1e4, 2) for k, v in sorted(area_by.items())
            },
            "lpa_area_ha": round(lpa_g.area / 1e4, 2),
            "px_area_m2": px_area,
            "raster": {
                "H": H,
                "W": W,
                "grid": grid,
                **stats,
                "max_fill_passes": max_pass,
            },
            "seconds": round(time.time() - t0, 1),
        },
    )


def _stream_polygonise(cls: np.ndarray, finish) -> None:
    """extract_plucomp.polygonise over BAND-row bands with the same final polygons: each
    band's per-value union; parts touching the band's top seam are unioned with the
    components left open by the band above; any component that does not reach the band's
    bottom seam is complete and handed to finish({value: [parts]}) at the end of the band."""
    import extract_plucomp as xp

    H = cls.shape[0]
    open_: dict[int, list] = {}
    for r0 in range(0, H, BAND):
        r1 = min(H, r0 + BAND)
        polys = xp.polygonise(cls[r0:r1])
        done: dict[int, list] = {}
        nxt: dict[int, list] = {}
        for v in set(polys) | set(open_):
            parts = []
            if v in polys:
                g = shapely.transform(polys.pop(v), lambda xy, r0=r0: xy + [0, r0])
                parts = list(shapely.get_parts(g))
            top = [p for p in parts if r0 > 0 and p.bounds[1] <= r0]
            rest = [p for p in parts if not (r0 > 0 and p.bounds[1] <= r0)]
            cand = open_.pop(v, []) + top
            if cand:
                u = shapely.union_all(np.array(cand, dtype=object), grid_size=1.0)
                rest += list(shapely.get_parts(u))
            for p in rest:
                if r1 < H and p.bounds[3] >= r1:
                    nxt.setdefault(v, []).append(p)
                else:
                    done.setdefault(v, []).append(p)
        open_ = nxt
        if done:
            finish(done)
        del done, polys
    if open_:
        finish(open_)


def _as_geom(parts):
    if not parts:
        return None
    return shapely.multipolygons(parts) if len(parts) > 1 else parts[0]


def _polygonise_banded(cls: np.ndarray) -> dict:
    """extract_plucomp.polygonise in BAND-row bands: each band's per-value union, then the
    parts that touch a band seam are dissolved across it. Returns value -> list of polygons,
    the same set as the parts of the one-pass union (pixel-grid coordinates, grid_size 1)."""
    import extract_plucomp as xp

    H = cls.shape[0]
    keep: dict[int, list] = {}
    seam: dict[int, list] = {}
    for r0 in range(0, H, BAND):
        r1 = min(H, r0 + BAND)
        polys = xp.polygonise(cls[r0:r1])
        for v, g in polys.items():
            g = shapely.transform(g, lambda xy, r0=r0: xy + [0, r0])
            for p in shapely.get_parts(g):
                _, miny, _, maxy = p.bounds
                if (r0 > 0 and miny <= r0) or (r1 < H and maxy >= r1):
                    seam.setdefault(v, []).append(p)
                else:
                    keep.setdefault(v, []).append(p)
    out = {}
    for v in set(keep) | set(seam):
        parts = list(keep.get(v, []))
        if seam.get(v):
            u = shapely.union_all(np.array(seam[v], dtype=object), grid_size=1.0)
            parts.extend(shapely.get_parts(u))
        out[v] = parts
    return out


# ---------------------------------------------------------------- Hoskote atlas sheet
def hsk_sheet(job: dict) -> None:
    import extract_hoskote as xh
    import pymupdf
    from extract_plucomp import fill_from_neighbours, polygonise

    t0 = time.time()
    doc = pymupdf.open(job["source_path"])
    page = doc[job["page"] - 1]
    fit = job["georef"]["grid_fit"]
    cls, (x0, y0, cpt, rpt) = xh.class_raster(page, doc)
    forest_px = xh.forest_regions(cls)
    halo_px = xh.drop_halos(cls)
    unknown = cls == xh.UNKNOWN
    unknown_pct = 100 * float(unknown.mean())
    cls = fill_from_neighbours(cls, unknown)
    del unknown
    m_px = abs(fit["E"][0]) * cpt
    g = xh.to_ground(fit)

    def px_to_ground(xy):
        return g(np.column_stack([x0 + xy[:, 0] * cpt, y0 + xy[:, 1] * rpt]))

    transport = cls == 10 + [c[1] for c in xh.CLASSES].index("transport")
    junctions = None
    if job.get("want_junctions"):
        sj, sdeg = xh.raster_junctions(transport, m_px, px_to_ground)
        junctions = {"xy": sj.tolist(), "deg": sdeg.tolist()}
    del transport
    polys = polygonise(np.where(cls >= 10, cls, 0).astype(np.uint8))
    del cls
    geoms, codes = [], []
    for code, gp in polys.items():
        if code < 10:
            continue
        for part in shapely.get_parts(
            shapely.make_valid(shapely.transform(gp, px_to_ground))
        ):
            for q in shapely.get_parts(part):
                if q.geom_type != "Polygon" or q.area < 0.5 * m_px * m_px:
                    continue
                geoms.append(q)
                codes.append(int(code))
    labels = {
        str(10 + i): {"zone_label_native": lb, "class_norm": cn}
        for i, (lb, cn, _c) in enumerate(xh.CLASSES)
    }
    area_by = {}
    for c, q in zip(codes, geoms, strict=True):
        k = labels[str(c)]["class_norm"]
        area_by[k] = area_by.get(k, 0.0) + q.area
    nzc = emit_chunks(b"ZONE", geoms, {"code": codes}, job.get("chunk_m", 2000.0))
    frame_json(
        b"META",
        {
            "labels": labels,
            "zones": len(geoms),
            "zone_chunks": nzc,
            "m_per_px": m_px,
            "unknown_px_pct": unknown_pct,
            "forest_px": forest_px,
            "halo_px_dropped": halo_px,
            "area_ha_by_class": {
                k: round(v / 1e4, 2) for k, v in sorted(area_by.items())
            },
            "junctions": junctions,
            "seconds": round(time.time() - t0, 1),
        },
    )


def hsk_outline(job: dict) -> None:
    import extract_hoskote as xh
    import pymupdf

    doc = pymupdf.open(job["source_path"])
    lpa, fit, nseg = xh.lpa_polygon(doc)
    lpa = shapely.union_all(
        [p for p in shapely.get_parts(lpa) if p.geom_type == "Polygon"]
    )
    frame(b"OUTL", shapely.to_wkb(lpa))
    frame_json(
        b"META",
        {
            "outline": {"authority": job.get("authority"), "extent": "plan"},
            "grid_fit": fit,
            "dash_segments": nseg,
            "area_km2": round(lpa.area / 1e6, 2),
        },
    )


def plucomp_outline(job: dict) -> None:
    import extract_plucomp as xp
    import pymupdf

    A = np.array(job["georef"]["affine_page_to_32643"])
    page = pymupdf.open(job["source_path"])[(job.get("page") or 1) - 1]
    lpa_page = xp.lpa_polygon_page(page)
    lpa = shapely.make_valid(
        shapely.transform(
            lpa_page, lambda xy: np.column_stack([xy, np.ones(len(xy))]) @ A
        )
    )
    frame(b"OUTL", shapely.to_wkb(lpa))
    frame_json(
        b"META",
        {
            "outline": {"authority": job.get("authority"), "extent": "plan"},
            "area_km2": round(lpa.area / 1e6, 2),
        },
    )


# ---------------------------------------------------------------- raster sheet with its own affine
def raster_sheet(job: dict) -> None:
    import pymupdf

    """Anekal / Nelamangala style: raster_plan.process_sheet with the stored affine, frame and
    palette (lpa=None: the service clips to the LPA), OSM check off."""
    import raster_plan as rp

    t0 = time.time()
    g = job["georef"]
    ex = job["extraction"]
    W, H = ex["image_size"]
    page_no = job.get("page") or 1
    src = job["source_path"]

    def load():
        if src.lower().endswith((".jpg", ".jpeg", ".png")):
            pix = pymupdf.Pixmap(src)
            if pix.n - pix.alpha != 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        elif ex.get("render"):
            # a vector sheet rendered at a fixed zoom over its map frame (exact colours, no
            # JPEG noise); the row's affine maps these pixels to EPSG:32643
            d = pymupdf.open(src)
            pg = d[page_no - 1]
            r = ex["render"]
            pix = pg.get_pixmap(
                matrix=pymupdf.Matrix(r["zoom"], r["zoom"]),
                clip=pymupdf.Rect(*r["clip_pt"]),
                alpha=False,
            )
        else:
            d = pymupdf.open(src)
            pg = d[page_no - 1]
            pix = pg.get_pixmap(
                matrix=pymupdf.Matrix(W / pg.rect.width, H / pg.rect.height)
            )
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
        return np.ascontiguousarray(a[..., :3])

    refine = None
    if ex.get("refine") == "anekal_detail":
        from extract_anekal import refine as refine_ank

        refine = refine_ank
    s = {
        "key": job["sheet_key"],
        "name": job["sheet"],
        "layer": job["source_layer"],
        "scale": ex.get("scale"),
        "doc_id": job["doc_id"],
        "A": g["affine_px_to_32643"],
        "map_rect": ex["map_rect"],
        "georef_method": g.get("method"),
        "georef_res_m": g.get("residual_m"),
        "classes": ex["classes"],
        "qa_warnings": [],
        "load": load,
        "refine": refine,
    }
    plan = {"status": job.get("status", "final")}
    meta = rp.process_sheet(
        s, plan, ex["classes"], None, None, job["work_dir"], ex["max_rgb_dist"]
    )
    import pyarrow.parquet as pq

    t = pq.read_table(os.path.join(job["work_dir"], f"map_{s['key']}.parquet"))
    geoms = list(shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False)))
    codes = t.column("code").to_pylist()
    del t
    for f in os.listdir(job["work_dir"]):
        os.remove(os.path.join(job["work_dir"], f))
    labels = {
        str(10 + i): {"zone_label_native": c["label"], "class_norm": c["cnorm"]}
        for i, c in enumerate(ex["classes"])
    }
    nzc = emit_chunks(b"ZONE", geoms, {"code": codes}, job.get("chunk_m", 2000.0))
    if job.get("outline_from_sheet"):
        o = job["outline_from_sheet"]
        u = shapely.union_all(np.array(geoms, dtype=object), grid_size=0.5)
        u = u.buffer(o["close_m"]).buffer(-o["close_m"])
        parts = [
            shapely.Polygon(p.exterior)
            for p in shapely.get_parts(u)
            if p.area > o["min_part_m2"]
        ]
        frame(b"OUTL", shapely.to_wkb(shapely.union_all(parts)))
    frame_json(
        b"META",
        {
            "labels": labels,
            "zones": len(geoms),
            "zone_chunks": nzc,
            "m_per_px": meta["m_per_px"],
            "unknown_px_pct": meta["unknown_px_pct"],
            "class_area_ha": meta["class_area_ha"],
            "outline": {"authority": job.get("authority"), "extent": "plan"}
            if job.get("outline_from_sheet")
            else None,
            "seconds": round(time.time() - t0, 1),
        },
    )


# ---------------------------------------------------------------- priority merge (service jobs)
def _read_chunks(path: str) -> dict[int, pa.Table]:
    """Chunk file written by the service: [u32 chunk index][u64 length][Arrow IPC] ..."""
    out = {}
    with open(path, "rb") as f:
        while True:
            head = f.read(12)
            if len(head) < 12:
                break
            i, n = struct.unpack("<IQ", head)
            out[i] = pa.ipc.open_stream(f.read(n)).read_all()
    return out


def _clip_pieces(g, clip, min_a):
    """The service's per-sheet clip (LPA outline): pieces outside are cut, slivers under
    min_a dropped. Returns (geoms, source index)."""
    if clip is None:
        return list(g), list(range(len(g)))
    shapely.prepare(clip)
    inside = shapely.contains(clip, g)
    res_g, res_i = [], []
    for k, (g0, ok) in enumerate(zip(g, inside, strict=True)):
        gg = g0 if ok else shapely.intersection(g0, clip)
        for q in shapely.get_parts(gg):
            if q.geom_type == "Polygon" and q.area >= min_a:
                res_g.append(q)
                res_i.append(k)
    return res_g, res_i


def _load_geom(path):
    if not path:
        return None
    with open(path, "rb") as f:
        return shapely.from_wkb(f.read())


def merge_batch(job: dict) -> None:
    """The service's priority merge, run here so its GEOS work stays out of the service.
    tasks: one sheet chunk each, clipped to its row's LPA and cut by the footprints (union of
    one higher-priority sheet chunk's clipped pieces) that touch each piece. tiles: LPA area
    on no sheet in one 2 km tile, the LPA minus every sheet footprint there. One ZONE frame
    per task, then one per tile, in job order (empty frames included)."""
    t0 = time.time()
    rows = job["rows"]
    tables = {rid: _read_chunks(r["path"]) for rid, r in rows.items()}
    clips = {rid: _load_geom(r.get("clip_path")) for rid, r in rows.items()}
    clipped: dict = {}
    feet: dict = {}

    def pieces(rid, ci):
        k = (rid, ci)
        if k not in clipped:
            t = tables[rid][ci]
            g = shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False))
            clipped[k] = _clip_pieces(g, clips[rid], rows[rid].get("min_area_m2", 0.0))
        return clipped[k]

    def foot(rid, ci):
        k = (rid, ci)
        if k not in feet:
            geoms = pieces(rid, ci)[0]
            if not geoms:
                f = None
            else:
                ga = np.array(geoms, dtype=object)
                try:
                    f = shapely.coverage_union_all(ga)
                except shapely.errors.GEOSException:
                    f = shapely.union_all(ga, grid_size=0.01)
                f = shapely.make_valid(f)
                if f.is_empty:
                    f = None
                else:
                    shapely.prepare(f)
            feet[k] = f
        return feet[k]

    for task in job.get("tasks", []):
        rid, ci = task["row"], task["chunk"]
        geoms, src = pieces(rid, ci)
        fl = [foot(h, hc) for h, hc in task.get("higher", [])]
        fa = np.array([f for f in fl if f is not None], dtype=object)
        grid = task.get("cut_grid")
        min_a = task.get("min_area_m2", 0.0)
        per_piece: dict[int, list] = {}
        if len(fa) and geoms:
            a_idx, b_idx = shapely.STRtree(np.array(geoms, dtype=object)).query(
                fa, predicate="intersects"
            )
            for fj, pj in zip(a_idx.tolist(), b_idx.tolist(), strict=True):
                per_piece.setdefault(pj, []).append(fa[fj])
        out_g, out_src = [], []
        if not per_piece:
            out_g, out_src = list(geoms), list(src)
        else:
            for j, q in enumerate(geoms):
                hits = per_piece.get(j)
                if hits:
                    bx = shapely.box(*q.bounds)
                    if grid:
                        cut = [
                            p
                            for c in hits
                            for p in shapely.get_parts(
                                shapely.intersection(c, bx, grid_size=grid)
                            )
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
                        q = shapely.difference(
                            q,
                            shapely.union_all(
                                [shapely.intersection(c, bx) for c in hits]
                            ),
                        )
                for p in shapely.get_parts(q):
                    if p.geom_type == "Polygon" and p.area >= min_a and not p.is_empty:
                        out_g.append(p)
                        out_src.append(src[j])
        t = tables[rid][ci].drop(["geometry"]).take(pa.array(out_src, pa.int64()))
        t = t.append_column("chunk", pa.array([ci] * len(out_g), pa.int64()))
        t = t.append_column("piece", pa.array(out_src, pa.int64()))
        t = t.append_column(
            "geometry",
            pa.array(
                shapely.to_wkb(np.array(out_g, dtype=object)) if out_g else [],
                pa.binary(),
            ),
        )
        frame(b"ZONE", ipc(t))
    lpa = _load_geom(job.get("lpa_path"))
    T = job.get("tile_m", 2000.0)
    for tl in job.get("tiles", []):
        ix, iy = tl["ix"], tl["iy"]
        tile = shapely.box(ix * T, iy * T, (ix + 1) * T, (iy + 1) * T)
        part = shapely.intersection(lpa, tile)
        geoms = []
        if not part.is_empty:
            fg = []
            for rid, ci in tl.get("feet", []):
                f = foot(rid, ci)
                if f is None or not f.intersects(tile):
                    continue
                fg.extend(
                    q
                    for q in shapely.get_parts(shapely.intersection(f, tile))
                    if q.geom_type == "Polygon"
                )
            if fg:
                cut = shapely.union_all(np.array(fg, dtype=object), grid_size=0.01)
                try:
                    part = shapely.difference(part, cut)
                except shapely.errors.GEOSException:
                    part = shapely.difference(part, cut, grid_size=0.01)
            geoms = [
                p
                for p in shapely.get_parts(part)
                if p.geom_type == "Polygon" and p.area >= tl.get("min_area_m2", 1.0)
            ]
        t = pa.table(
            {
                "geometry": pa.array(
                    shapely.to_wkb(np.array(geoms, dtype=object)) if geoms else [],
                    pa.binary(),
                )
            }
        )
        frame(b"ZONE", ipc(t))
    frame_json(
        b"META",
        {
            "tasks": len(job.get("tasks", [])),
            "tiles": len(job.get("tiles", [])),
            "footprints": len(feet),
            "seconds": round(time.time() - t0, 1),
        },
    )


# ---------------------------------------------------------------- vector plan sheet (GIS export)
def vector_fill_sheet(job: dict) -> None:
    """A land-use map exported from GIS as vector fills (e.g. Anekal Map No. 39): each filled
    drawing inside the map frame whose colour is a legend class is one zone polygon. Paint
    order is respected (later fills cover earlier ones); fills whose colour is listed as an
    annotation (callouts, symbols) are skipped. Page points -> EPSG:32643 by the sheet's own
    grid-label fit (E = aE x + bE, N = aN y + bN)."""
    import lpa_map_bmrda as lm
    import pymupdf

    t0 = time.time()
    ex = job["extraction"]
    fit = job["georef"]["grid_fit"]
    aE, bE = fit["E"][:2]
    aN, bN = fit["N"][:2]
    frame_box = shapely.box(*ex["frame_pt"])
    code_of = {}
    labels = {}
    for i, c in enumerate(ex["classes"]):
        code = 10 + i
        labels[str(code)] = {"zone_label_native": c["label"], "class_norm": c["cnorm"]}
        for h in c["fills"]:
            code_of[h.lower()] = code
    skip = {h.lower() for h in ex.get("annotation_fills", [])}

    def hx(col):
        return "#" + "".join(f"{round(v * 255):02x}" for v in col[:3])

    pg = pymupdf.open(job["source_path"])[(job.get("page") or 1) - 1]
    seq = []
    for dr in pg.get_drawings():
        f = dr.get("fill")
        if f is None:
            continue
        h = hx(f)
        if h in skip or h not in code_of:
            continue
        if not shapely.box(*dr["rect"]).intersects(frame_box):
            continue
        g = lm.even_odd(lm.rings(dr))
        if g.is_empty:
            continue
        seq.append((code_of[h], g))
    # reverse paint order: each fill keeps only what later fills leave visible
    geoms, codes = [], []
    covered: list = []
    tree, tree_n = None, 0
    for code, g in reversed(seq):
        g = shapely.intersection(g, frame_box)
        if g.is_empty:
            continue
        if covered:
            if tree is None or len(covered) - tree_n > 500:
                tree, tree_n = (
                    shapely.STRtree(np.array(covered, dtype=object)),
                    len(covered),
                )
            idx = tree.query(g, predicate="intersects")
            cand = [covered[j] for j in idx] + covered[tree_n:]
            # neighbours that only share an edge need no cut; only true overlaps do
            hits = [c for c in cand if c.intersects(g) and not c.touches(g)]
            if hits:
                g = shapely.difference(g, shapely.union_all(hits))
        covered.append(g)
        for q in shapely.get_parts(shapely.make_valid(g)):
            if q.geom_type != "Polygon" or q.is_empty:
                continue
            q = shapely.transform(
                q,
                lambda xy: np.column_stack([aE * xy[:, 0] + bE, aN * xy[:, 1] + bN]),
            )
            # a ring that nearly touches itself can turn invalid after the transform
            parts = [q] if q.is_valid else shapely.get_parts(shapely.make_valid(q))
            for p in parts:
                if p.geom_type == "Polygon" and p.area >= ex.get("min_area_m2", 1.0):
                    geoms.append(p)
                    codes.append(code)
    del covered, tree, seq
    area_by: dict = {}
    for c, q in zip(codes, geoms, strict=True):
        k = labels[str(c)]["class_norm"] or labels[str(c)]["zone_label_native"]
        area_by[k] = area_by.get(k, 0.0) + q.area
    nzc = emit_chunks(b"ZONE", geoms, {"code": codes}, job.get("chunk_m", 2000.0))
    if job.get("outline_from_sheet"):
        o = job["outline_from_sheet"]
        valid = shapely.make_valid(np.array(geoms, dtype=object))
        try:
            u = shapely.union_all(valid, grid_size=0.5)
        except shapely.errors.GEOSException:
            u = shapely.union_all(shapely.buffer(valid, 0.5)).buffer(-0.5)
        u = u.buffer(o["close_m"]).buffer(-o["close_m"])
        parts = [
            shapely.Polygon(p.exterior)
            for p in shapely.get_parts(u)
            if p.area > o["min_part_m2"]
        ]
        frame(b"OUTL", shapely.to_wkb(shapely.union_all(parts)))
    frame_json(
        b"META",
        {
            "labels": labels,
            "zones": len(geoms),
            "zone_chunks": nzc,
            "area_ha_by_class": {
                k: round(v / 1e4, 2) for k, v in sorted(area_by.items())
            },
            "outline": {"authority": job.get("authority"), "extent": "plan"}
            if job.get("outline_from_sheet")
            else None,
            "seconds": round(time.time() - t0, 1),
        },
    )


# ---------------------------------------------------------------- BMRDA LPA map outlines
def bmrda_lpa_map(job: dict) -> None:
    import lpa_map_bmrda as lm
    import pymupdf

    A = job["georef"]["affine_page_to_32643"]
    page = pymupdf.open(job["source_path"])[(job.get("page") or 1) - 1]
    polys = lm.fills(page)
    out = []
    for h, (code, name, km2) in lm.FILLS.items():
        if h not in polys:
            continue
        full, vis = (shapely.make_valid(lm.affine_apply(A, x)) for x in polys[h])
        for kind, g in (("current", vis), ("pre_strr", full)):
            if kind == "pre_strr" and code == "STRR":
                continue
            frame(b"OUTL", shapely.to_wkb(g))
            out.append(
                {"authority": code, "name": name, "extent": kind, "legend_km2": km2}
            )
    allu = shapely.union_all([f for f, _v in polys.values()])
    holes = [shapely.Polygon(r) for p in shapely.get_parts(allu) for r in p.interiors]
    bda_map = max(holes, key=lambda hh: hh.area)
    frame(b"OUTL", shapely.to_wkb(shapely.make_valid(lm.affine_apply(A, bda_map))))
    out.append(
        {
            "authority": "BDA",
            "name": "BDA LPA (unfilled on the map)",
            "extent": "current",
            "legend_km2": lm.BDA_KM2,
        }
    )
    frame_json(b"META", {"outlines": out})


def bmrda_lpa_outline(job: dict) -> None:
    """One authority's outline from the BMRDA LPA map (e.g. Anekal's pre-STRR extent, the
    LPA its 2031 plan was made for). Emits one OUTL with extent "plan"."""
    import lpa_map_bmrda as lm
    import pymupdf

    ex = job["extraction"]
    A = job["georef"]["affine_page_to_32643"]
    page = pymupdf.open(job["source_path"])[(job.get("page") or 1) - 1]
    polys = lm.fills(page)
    h = next(hh for hh, (c, _n, _k) in lm.FILLS.items() if c == ex["authority"])
    full, vis = (shapely.make_valid(lm.affine_apply(A, x)) for x in polys[h])
    g = full if ex.get("extent", "pre_strr") == "pre_strr" else vis
    frame(b"OUTL", shapely.to_wkb(g))
    name, km2 = lm.FILLS[h][1:]
    frame_json(
        b"META",
        {
            "outlines": [
                {
                    "authority": ex["authority"],
                    "name": name,
                    "extent": "plan",
                    "from": f"BMRDA LPA map, {ex.get('extent', 'pre_strr')} extent",
                    "legend_km2": km2,
                    "area_km2": round(g.area / 1e6, 2),
                }
            ]
        },
    )


def fixture_parquet(job: dict) -> None:
    """Test-only: serve a GeoParquet of finished zones / overlays / an outline (smoke tests)."""
    import pyarrow.parquet as pq

    t = pq.read_table(job["source_path"])
    g = list(shapely.from_wkb(t.column("geometry").to_numpy(zero_copy_only=False)))
    what = job["extraction"]["fixture"]
    if what == "outline":
        frame(b"OUTL", shapely.to_wkb(shapely.union_all(np.array(g, dtype=object))))
        frame_json(
            b"META", {"outline": {"authority": job.get("authority"), "extent": "plan"}}
        )
        return
    if what == "overlays":
        emit_chunks(
            b"OVLY",
            g,
            {
                "kind": t.column("class_norm").to_pylist(),
                "label": t.column("overlay_label_native").to_pylist(),
                "method": t.column("method").to_pylist(),
            },
            job.get("chunk_m", 2000.0),
        )
        frame_json(b"META", {"overlays": len(g)})
        return
    labels, codes = {}, []
    names = t.column("zone_label_native").to_pylist()
    cn = t.column("class_norm").to_pylist()
    for lb, c in zip(names, cn, strict=True):
        k = next((kk for kk, v in labels.items() if v["zone_label_native"] == lb), None)
        if k is None:
            k = str(10 + len(labels))
            labels[k] = {"zone_label_native": lb, "class_norm": c}
        codes.append(int(k))
    cols = {"code": codes, "note": t.column("note").to_pylist()}
    if "cartographic" in t.column_names:
        cols["cartographic"] = [bool(x) for x in t.column("cartographic").to_pylist()]
    emit_chunks(b"ZONE", g, cols, job.get("chunk_m", 2000.0))
    frame_json(b"META", {"labels": labels, "zones": len(g)})


METHODS = {
    "fixture_parquet": fixture_parquet,
    "plucomp_composite": plucomp,
    "plucomp_outline": plucomp_outline,
    "hsk_atlas_sheet": hsk_sheet,
    "hsk_atlas_outline": hsk_outline,
    "raster_affine_sheet": raster_sheet,
    "vector_fill_sheet": vector_fill_sheet,
    "merge_batch": merge_batch,
    "bmrda_lpa_map": bmrda_lpa_map,
    "bmrda_lpa_outline": bmrda_lpa_outline,
}


def main() -> None:
    job = json.loads(sys.stdin.buffer.read())
    METHODS[job["extraction"]["method"]](job)
    frame_json(b"DONE", {"ok": True})


if __name__ == "__main__":
    main()
