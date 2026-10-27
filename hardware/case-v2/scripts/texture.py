"""Гранёная фактура на видимых боковых стенках, запечённая в сетку.

Режимы:
  tri — low-poly: треугольная решётка, в узлах случайная высота, внутри треугольника — плоскость;
  hex — соты: шестигранные плитки с плоским верхом и фаской к шву.
Почему не fuzzy skin слайсера: он шершавит все внешние контуры слоя — стенки кармана клипсы, бортик под
крышку, упоры внутри крышки — и посадки перестают сходиться. Здесь рельеф только там, где его видно:
вершины не дальше tol от контура стенки и в диапазоне высот Y. Смещение только наружу (стенку не утоняет),
с плавным затуханием у краёв полосы. Рисунок строится на развёртке стенки (длина по контуру, Y) и
замкнут по длине контура — без шва на стыке.
"""
import numpy as np
import shapely
from shapely.geometry import Polygon

PARAMS = {
    # шаг решётки, мм; высота рельефа, мм; фаска (только hex), мм
    "tri": dict(step=3.0, depth=0.5),
    "hex": dict(step=3.0, depth=0.35, bevel=0.45),
}
EDGE = 0.2       # длина ребра сетки после дробления, мм: грани и фаски должны выйти плоскими, а швы — острыми
FADE = 0.5       # затухание рельефа у границ полосы по Y, мм


def _hash(i, j, seed):
    """Детерминированное «случайное» 0…1 по целым координатам узла."""
    x = np.sin(i * 12.9898 + j * 78.233 + seed * 37.719) * 43758.5453
    return x - np.floor(x)


def _tri(u, v, length, step, depth, seed):
    """Каждый треугольник решётки — низкая трёхгранная пирамидка (вершина в центре), высота своя у каждой:
    острые рёбра и много мелких плоских граней. Плавная интерполяция высот по узлам давала «волны»."""
    n = max(3, int(round(length / step)))
    s = length / n                                  # шаг, кратный длине контура: рисунок замыкается
    h = s * np.sqrt(3) / 2
    fj = v / h
    j = np.floor(fj)
    fi = (u - j * s / 2) / s
    i = np.floor(fi)
    a, b = fi - i, fj - j
    lower = a + b < 1
    # барицентрические координаты в своём треугольнике; min = доля пути от ребра к центру (у центра 1/3)
    w = np.where(lower, np.minimum(np.minimum(1 - a - b, a), b), np.minimum(np.minimum(a + b - 1, 1 - a), 1 - b))
    tri_id = 2 * np.mod(i, n) + lower
    scale = 0.45 + 0.55 * _hash(tri_id, j, seed)
    return depth * scale * np.clip(3 * w, 0, 1)


def _hex(u, v, length, step, depth, bevel, seed):
    n = max(3, int(round(length / step)))
    s = length / n
    h = s * np.sqrt(3) / 2
    j0 = np.floor(v / h)
    best = np.full(u.shape, np.inf)
    cu, cv = np.zeros_like(u), np.zeros_like(u)
    for dj in (-1, 0, 1, 2):
        j = j0 + dj
        off = np.mod(j, 2) * s / 2
        i0 = np.round((u - off) / s)
        for di in (-1, 0, 1):
            pu, pv = (i0 + di) * s + off, j * h
            d = (u - pu) ** 2 + (v - pv) ** 2
            take = d < best
            best = np.where(take, d, best)
            cu, cv = np.where(take, pu, cu), np.where(take, pv, cv)
    du, dv = u - cu, v - cv
    # до ребра шестигранника (центры на треугольной решётке → ячейка с рёбрами поперёк 0°, 60°, 120°)
    proj = np.max([np.abs(du * np.cos(a) + dv * np.sin(a)) for a in np.radians([0, 60, 120])], axis=0)
    edge = s / 2 - proj
    return depth * np.clip(edge / bevel, 0, 1)


def apply(man, outline, y_lo, y_hi, tol=0.3, seed=1, mode="tri"):
    """man — Manifold; outline — Polygon стенки в плоскости X–Z; рельеф на вершинах у контура при y_lo ≤ y ≤ y_hi."""
    outline = Polygon(outline.exterior.coords)
    ring = outline.exterior
    length = ring.length
    prm = PARAMS[mode]

    def warp(p):
        p = np.array(p, dtype=np.float64)
        x, y, z = p[:, 0], p[:, 1], p[:, 2]
        pts = shapely.points(x, z)
        d = shapely.distance(ring, pts)
        sel = (d < tol) & (y > y_lo) & (y < y_hi)
        if not sel.any():
            return p
        ps = pts[sel]
        pos = shapely.line_locate_point(ring, ps)
        a = shapely.get_coordinates(shapely.line_interpolate_point(ring, np.mod(pos - 0.05, length)))
        b = shapely.get_coordinates(shapely.line_interpolate_point(ring, np.mod(pos + 0.05, length)))
        t = b - a
        t /= np.linalg.norm(t, axis=1, keepdims=True)
        nrm = np.c_[t[:, 1], -t[:, 0]]
        probe = np.c_[x[sel], z[sel]] + nrm * 0.5
        nrm[shapely.contains_xy(outline, probe[:, 0], probe[:, 1])] *= -1
        if mode == "tri":
            field = _tri(pos, y[sel], length, prm["step"], prm["depth"], seed)
        else:
            field = _hex(pos, y[sel], length, prm["step"], prm["depth"], prm["bevel"], seed)
        w = np.clip((y[sel] - y_lo) / FADE, 0, 1) * np.clip((y_hi - y[sel]) / FADE, 0, 1)
        w *= np.clip((tol - d[sel]) / 0.1, 0, 1)
        disp = field * w
        p[sel, 0] += nrm[:, 0] * disp
        p[sel, 2] += nrm[:, 1] * disp
        return p

    return man.refine_to_length(EDGE).warp_batch(warp)
