"""Лицевая сторона крышки в посадке: отверстия и панель — для размещения эмблемы."""
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
OUT = str(CASE_DIR / "out")
c = trimesh.load(f"{OUT}\\v2_cover_seated.stl")
print("крышка y:", np.round(c.bounds[:, 1], 2))
fig, axes = plt.subplots(1, 3, figsize=(18, 6))
for ax, y in zip(axes, (11.9, 11.6, 11.2)):
    sec = c.section(plane_origin=[0, y, 0], plane_normal=[0, 1, 0])
    polys = sorted([Polygon(np.c_[e[:, 0], e[:, 2]]) for e in sec.discrete], key=lambda p: -p.area)
    print(f"y={y}: контуров {len(polys)}")
    for p in polys:
        x, z = p.exterior.xy
        ax.plot(x, z, "-", lw=0.8)
        if p.area < 30:
            print(f"   малый контур: центр ({p.centroid.x:.2f}, {p.centroid.y:.2f}), площадь {p.area:.2f}, габарит {np.round(p.bounds, 2)}")
        else:
            print(f"   контур: площадь {p.area:.1f}, габарит {np.round(p.bounds, 2)}")
    ax.set_aspect("equal"); ax.grid(alpha=0.3); ax.set_title(f"срез крышки y={y} (система корпуса)")
    ax.invert_xaxis()  # вид снаружи (с +Y): X направлен влево
plt.tight_layout(); plt.savefig(f"{OUT}\\cover_face.png", dpi=65)
