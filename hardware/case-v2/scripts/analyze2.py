"""Где в корпусе вырезы (USB, выключатель), ушко и сколько места внутри."""
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
import sys

import matplotlib
import numpy as np
import trimesh
from shapely.geometry import Polygon

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.stdout.reconfigure(encoding="utf-8")
SRC = str(CASE_DIR / "omi_triangle_v1")
OUT = str(CASE_DIR / "out")
base = trimesh.load(f"{SRC}\\base.stl")
print("watertight", base.is_watertight, "winding", base.is_winding_consistent, "тел", base.body_count)
fixed = base.copy()
trimesh.repair.fill_holes(fixed)
trimesh.repair.fix_normals(fixed)
print("после ремонта watertight", fixed.is_watertight, "объём", round(fixed.volume, 1))

# Срезы плоскостями, параллельными граням: нижняя грань (z≈5)
fig, axes = plt.subplots(1, 3, figsize=(18, 5))
for a, z in zip(axes, (4.2, 5.0, 5.8)):
    sec = base.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if sec is not None:
        for ent in sec.discrete:
            a.plot(ent[:, 0], ent[:, 1], "k-", lw=0.8)
    a.set_title(f"срез z={z} (нижняя грань), оси X–Y"); a.set_aspect("equal"); a.grid(alpha=0.3)
plt.tight_layout(); plt.savefig(f"{OUT}\\cut_bottom.png", dpi=70)

# Внутренний контур кармана на уровне y=3 (над дном)
sec = base.section(plane_origin=[0, 3.0, 0], plane_normal=[0, 1, 0])
polys = []
for ent in sec.discrete:
    p = Polygon(np.c_[ent[:, 0], ent[:, 2]])
    if p.is_valid and p.area > 1:
        polys.append(p)
polys.sort(key=lambda p: -p.area)
outer, inner = polys[0], polys[1]
print("снаружи: площадь %.0f, габарит %s" % (outer.area, np.round(outer.bounds, 2)))
print("внутри:  площадь %.0f, габарит %s" % (inner.area, np.round(inner.bounds, 2)))
for zz in (8, 12, 16, 20, 24, 28, 32, 36, 39):
    line = trimesh.load_path(np.array([[[-5, zz], [50, zz]]]))
    from shapely.geometry import LineString
    seg = inner.intersection(LineString([(-5, zz), (50, zz)]))
    segs = [seg] if seg.geom_type == "LineString" else list(getattr(seg, "geoms", []))
    print(f"  z={zz}: внутри по X", [tuple(np.round(s.bounds[::2], 1)) for s in segs])
np.save(f"{OUT}\\outer_xz.npy", np.array(outer.exterior.coords))
np.save(f"{OUT}\\inner_xz.npy", np.array(inner.exterior.coords))

# Глубина: дно и верх стенки
ys = base.vertices[:, 1]
print("y уровней (часто встречающиеся):", np.unique(np.round(ys, 2))[:40])
