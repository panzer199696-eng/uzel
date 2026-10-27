"""Эмблема УЗЛА (петля-«узел» из ic_launcher_mono.xml) утопленной гравировкой на лицо крышки."""
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
import re

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

ICON = CASE_DIR.parents[1] / "bridge/android/app/src/main/res/drawable/ic_launcher_mono.xml"
UP = np.array([np.cos(np.radians(330)), np.sin(np.radians(330))])   # «вверх» при ношении USB вниз
RIGHT = np.array([np.cos(np.radians(60)), np.sin(np.radians(60))])  # «вправо» для взгляда снаружи (+Y)


def icon_shape():
    """Контур эмблемы в координатах иконки (Y вниз), вместе с толщиной линии."""
    xml = open(ICON, encoding="utf-8").read()
    path = re.search(r'pathData="([^"]+)"', xml).group(1)
    width = float(re.search(r'strokeWidth="([0-9.]+)"', xml).group(1))
    pts = [tuple(map(float, p.split(","))) for p in re.findall(r"[-0-9.]+,[-0-9.]+", path)]
    line = LineString(pts + [pts[0]])
    return line.buffer(width / 2, cap_style=1, join_style=1), line


def placed(shape, size, center):
    """Эмблема шириной size мм с центром center (x, z) в системе корпуса, повёрнутая «вверх» по ношению."""
    minx, miny, maxx, maxy = shape.bounds
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    k = size / (maxx - minx)
    # (xi, yi) -> right = (xi-cx)k, up = -(yi-cy)k -> x,z = C + right*RIGHT + up*UP
    a, b = k * RIGHT, -k * UP
    m = [a[0], b[0], a[1], b[1], center[0] - a[0] * cx - b[0] * cy, center[1] - a[1] * cx - b[1] * cy]
    return affinity.affine_transform(shape, m)


def fit(panel, holes, hole_margin=1.5, edge_margin=1.2, sizes=np.arange(18, 8, -0.5)):
    """Самая крупная эмблема, которая влезает в панель и не подходит к отверстиям."""
    shape, _ = icon_shape()
    inner = panel.buffer(-edge_margin)
    keep_out = unary_union([h.buffer(hole_margin) for h in holes])
    minx, minz, maxx, maxz = inner.bounds
    for size in sizes:
        best = None
        for x in np.arange(minx, maxx, 0.4):
            for z in np.arange(minz, maxz, 0.4):
                e = placed(shape, size, (x, z))
                if not inner.contains(e) or e.intersects(keep_out):
                    continue
                score = min(inner.exterior.distance(e), keep_out.distance(e))
                if best is None or score > best[0]:
                    best = (score, (x, z), e)
        if best:
            return size, best[1], best[2], best[0]
    return None
