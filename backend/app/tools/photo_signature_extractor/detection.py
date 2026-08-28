"""
Automatic photo / signature grid detection.

This is a faithful Python port of the computer-vision pipeline originally
written in vanilla JavaScript inside the client-side prototype
(`Photo Sign Extractor.html`). Function names and constants intentionally
mirror the original JS 1:1 so the two can be diffed against each other.

Ported constants (do not change without re-validating against the golden
test set in tests/golden/):
  - saturation threshold for "is this a photo" (0.12)
  - aspect ratio bounds for a plausible photo/signature box (0.4-2.2)
  - minimum blob area as a fraction of page area (0.005)
  - row/column clustering tolerance (0.08 of page dimension)
  - box padding (5%)
  - signature search window caps (68% of photo height, 55% of row spacing)
  - dark-pixel threshold for "ink" (140 of 255 grayscale)

2026-08 fix: `find_signature_box`'s run-selection logic was reworked. The
original JS excluded any ink run sitting in the last 35% of the search
window, assuming that was always the printed "Signature within the box"
caption. That's a window-relative-position heuristic and it doesn't
generalize -- the window's own height varies per cell, so it both (a)
excluded genuine signatures that happened to sit late in a short window,
and (b) admitted ink bleeding over from the photo itself (e.g. a patterned
collar right below the photo's bottom edge) when nothing else was found.
Replaced with two content-based signals: MIN_GAP (a run touching the
photo's own bottom edge is bleed-over, not signature ink) and
MAX_SPAN_FRAC (the printed caption sentence spans nearly the full box
width; a handwritten name normally doesn't). See
project doc `signature-crop-bug-fix.md` for the full root-cause writeup.
"""
import numpy as np
from scipy import ndimage

SATURATION_THRESH = 0.12
ASPECT_MIN, ASPECT_MAX = 0.4, 2.2
MIN_AREA_FRAC = 0.005
CLUSTER_TOL_FRAC = 0.08
BOX_PAD_FRAC = 0.05
SIG_CAP_FRAC = 0.68
SIG_ROW_SPACING_FRAC = 0.55
DARK_THRESH = 140
SIG_MIN_GAP_PX = 20
SIG_MAX_SPAN_FRAC = 0.72


def get_masks(img: np.ndarray):
    """RGB (H,W,3) uint8 -> (saturation, value, grayscale) float arrays, HSV-ish."""
    img = img.astype(np.float32)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = (mx - mn) / np.maximum(mx, 1)
    val = mx / 255.0
    gray = 0.299 * r + 0.587 * g + 0.114 * b
    return sat, val, gray


def _binary_open_3x3(mask: np.ndarray) -> np.ndarray:
    structure = np.ones((3, 3), dtype=bool)
    eroded = ndimage.binary_erosion(mask, structure=structure, border_value=0)
    return ndimage.binary_dilation(eroded, structure=structure, border_value=0)


def _connected_component_boxes(mask: np.ndarray, min_area: float):
    structure = np.ones((3, 3), dtype=int)  # 8-connectivity, matches the JS flood fill
    labeled, _ = ndimage.label(mask, structure=structure)
    boxes = []
    for i, sl in enumerate(ndimage.find_objects(labeled)):
        if sl is None:
            continue
        ys, xs = sl
        area = int((labeled[sl] == (i + 1)).sum())
        if area >= min_area:
            boxes.append({"x0": xs.start, "y0": ys.start, "x1": xs.stop - 1, "y1": ys.stop - 1, "area": area})
    return boxes


def _find_saturated_boxes(sat: np.ndarray, thresh: float):
    h, w = sat.shape
    mask = sat > thresh
    opened = _binary_open_3x3(mask)
    min_area = w * h * MIN_AREA_FRAC
    boxes = _connected_component_boxes(opened, min_area)
    out = []
    for b in boxes:
        bw, bh = b["x1"] - b["x0"], b["y1"] - b["y0"]
        aspect = bw / max(bh, 1)
        if ASPECT_MIN <= aspect <= ASPECT_MAX:
            out.append(b)
    return out


def _cluster_centers(centers, tol_frac, total):
    idx = sorted(range(len(centers)), key=lambda i: centers[i])
    groups, cur = [], [idx[0]]
    for k in range(1, len(idx)):
        i = idx[k]
        if centers[i] - centers[cur[-1]] > tol_frac * total:
            groups.append(cur)
            cur = []
        cur.append(i)
    groups.append(cur)
    return groups


def _median(arr):
    return float(np.median(arr)) if len(arr) else 0.0


def _mean(arr):
    return float(np.mean(arr)) if len(arr) else 0.0


def _pad_box(box, w, h, pad_frac):
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    px, py = bw * pad_frac, bh * pad_frac
    return [max(0, x0 - px), max(0, y0 - py), min(w, x1 + px), min(h, y1 + py)]


def _closest_index(arr, v):
    return min(range(len(arr)), key=lambda i: abs(arr[i] - v))


def _fallback_find_box(val, sx0, sy0, sx1, sy1):
    """Recover a placeholder cell (printed box/handwriting but no real photo,
    e.g. a "photo not available" note) by finding the largest non-white blob
    in the expected sub-region. Returns None if the region is truly blank."""
    rw, rh = sx1 - sx0, sy1 - sy0
    if rw <= 0 or rh <= 0:
        return None
    region = val[sy0:sy1, sx0:sx1]
    mask = region < 0.90
    boxes = _connected_component_boxes(mask, 1500)
    best, best_area = None, 0
    for b in boxes:
        bw, bh = b["x1"] - b["x0"], b["y1"] - b["y0"]
        aspect = bw / max(bh, 1)
        if not (ASPECT_MIN <= aspect <= ASPECT_MAX):
            continue
        area = bw * bh
        if area > best_area:
            best_area = area
            best = [sx0 + b["x0"], sy0 + b["y0"], sx0 + b["x1"], sy0 + b["y1"]]
    return best


def detect_photo_grid(img: np.ndarray):
    """Detect the (up to) 3x3 grid of photo boxes on a page image.

    Returns a dict with `grid` keyed by (row, col) -> [x0,y0,x1,y1], plus the
    row/column geometry needed by find_signature_box. A grid cell with no
    detectable content at all (truly blank) is simply absent from `grid`.
    """
    sat, val, gray = get_masks(img)
    h, w = sat.shape
    raw_boxes = _find_saturated_boxes(sat, SATURATION_THRESH)
    if not raw_boxes:
        return {"grid": {}, "rowYFull": [], "colXFull": [], "medW": 0, "medH": 0, "rowSpacing": 0, "w": w, "h": h, "gray": gray}

    centers_x = [(b["x0"] + b["x1"]) / 2 for b in raw_boxes]
    centers_y = [(b["y0"] + b["y1"]) / 2 for b in raw_boxes]
    row_groups = _cluster_centers(centers_y, CLUSTER_TOL_FRAC, h)
    col_groups = _cluster_centers(centers_x, CLUSTER_TOL_FRAC, w)
    rowYFull = sorted(_mean([centers_y[i] for i in g]) for g in row_groups)
    colXFull = sorted(_mean([centers_x[i] for i in g]) for g in col_groups)
    medW = _median([b["x1"] - b["x0"] for b in raw_boxes])
    medH = _median([b["y1"] - b["y0"] for b in raw_boxes])

    grid, used = {}, set()
    for b in raw_boxes:
        cx, cy = (b["x0"] + b["x1"]) / 2, (b["y0"] + b["y1"]) / 2
        key = (_closest_index(rowYFull, cy), _closest_index(colXFull, cx))
        grid[key] = [b["x0"], b["y0"], b["x1"], b["y1"]]
        used.add(key)

    for r in range(len(rowYFull)):
        for c in range(len(colXFull)):
            key = (r, c)
            if key in used:
                continue
            cxEst, cyEst = colXFull[c], rowYFull[r]
            sx0 = max(0, round(cxEst - medW * 0.75)); sx1 = min(w, round(cxEst + medW * 0.75))
            sy0 = max(0, round(cyEst - medH * 0.75)); sy1 = min(h, round(cyEst + medH * 0.75))
            best = _fallback_find_box(val, sx0, sy0, sx1, sy1)
            if best:
                grid[key] = best
            # else: nothing printed here at all -- leave this slot out entirely

    for key in grid:
        grid[key] = _pad_box(grid[key], w, h, BOX_PAD_FRAC)

    rowSpacing = medH * 2.5
    if len(rowYFull) >= 2:
        diffs = [rowYFull[d] - rowYFull[d - 1] for d in range(1, len(rowYFull))]
        rowSpacing = _median(diffs)

    return {"grid": grid, "rowYFull": rowYFull, "colXFull": colXFull, "medW": medW, "medH": medH, "rowSpacing": rowSpacing, "w": w, "h": h, "gray": gray}


def _runs_from_indices(indices, max_gap):
    if not indices:
        return []
    runs, start, prev = [], indices[0], indices[0]
    for i in indices[1:]:
        if i - prev > max_gap:
            runs.append((start, prev))
            start = i
        prev = i
    runs.append((start, prev))
    return runs


def find_signature_box(gray: np.ndarray, w: int, h: int, box, ri: int, rowYFull, rowSpacing, cap_frac: float = SIG_CAP_FRAC):
    """Locate a student's signature in the bounded region below their photo box."""
    x0, y0, x1, y1 = box
    pw, ph = x1 - x0, y1 - y0
    sx0 = max(0, round(x0 - pw * 0.15))
    sx1 = min(w, round(x1 + pw * 0.15))
    sy0 = round(y1 + 8)
    sy1a = y1 + ph * cap_frac
    sy1b = rowYFull[ri] + rowSpacing * SIG_ROW_SPACING_FRAC
    sy1 = round(max(min(sy1a, sy1b, h), sy0 + 5))

    rw, rh = sx1 - sx0, sy1 - sy0
    if rw < 5 or rh < 5:
        return [sx0, sy0, sx1, sy1]

    region_gray = gray[sy0:sy1, sx0:sx1]
    dark = region_gray < DARK_THRESH
    row_sum = dark.sum(axis=1)
    row_thresh = max(2, rw * 0.008)
    rows_with_ink = [y for y in range(rh) if row_sum[y] > row_thresh]
    runs = _runs_from_indices(rows_with_ink, 10)
    if not runs:
        return [sx0, sy0, sx1, min(sy1, sy0 + 30)]

    def span_frac(a, b):
        colmask = dark[a:b + 1, :].any(axis=0)
        cols = np.where(colmask)[0]
        return 0.0 if len(cols) == 0 else (cols[-1] - cols[0]) / rw

    filtered = [r for r in runs if r[0] >= SIG_MIN_GAP_PX and span_frac(*r) <= SIG_MAX_SPAN_FRAC]
    candidates = filtered if filtered else runs

    chosen, best_height, best_ink = None, -1, -1
    for a, b in candidates:
        height = b - a
        ink = int(row_sum[a:b + 1].sum())
        if height > best_height or (height == best_height and ink > best_ink):
            chosen, best_height, best_ink = (a, b), height, ink
    if chosen is None:
        return [sx0, sy0, sx1, min(sy1, sy0 + 30)]

    ry0, ry1 = chosen
    col_region = region_gray[ry0:ry1 + 1, :]
    col_sum = (col_region < DARK_THRESH).sum(axis=0)
    col_thresh = max(1, (ry1 - ry0 + 1) * 0.01)
    cols_with_ink = [x for x in range(rw) if col_sum[x] > col_thresh]
    if not cols_with_ink:
        return [sx0, sy0, sx1, min(sy1, sy0 + 30)]
    rx0, rx1 = cols_with_ink[0], cols_with_ink[-1]
    padx = round((rx1 - rx0) * 0.12) + 5
    pady = round((ry1 - ry0) * 0.30) + 5
    gx0 = max(sx0, sx0 + rx0 - padx); gx1 = min(sx1, sx0 + rx1 + padx)
    gy0 = max(sy0, sy0 + ry0 - pady); gy1 = min(sy1, sy0 + ry1 + pady)
    return [gx0, gy0, gx1, gy1]
