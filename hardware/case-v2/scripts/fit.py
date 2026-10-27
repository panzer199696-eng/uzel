"""Как крышка садится на корпус: подбор преобразования по совпадению контуров ободка."""
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
import sys

import numpy as np
import trimesh
from shapely.geometry import Polygon
from shapely import affinity

sys.stdout.reconfigure(encoding="utf-8")
SRC = str(CASE_DIR / "omi_triangle_v1")
base = trimesh.load(f"{SRC}\\base.stl")
cover = trimesh.load(f"{SRC}\\top_cover.STL")


def outline(mesh, y, which="all"):
    sec = mesh.section(plane_origin=[0, y, 0], plane_normal=[0, 1, 0])
    polys = [Polygon(np.c_[e[:, 0], e[:, 2]]) for e in sec.discrete]
    polys = [p.buffer(0) for p in polys if p.area > 0.5]
    polys.sort(key=lambda p: -p.area)
    return polys


# Профиль стенки корпуса по высоте: внешний и внутренний контур
for y in (6.5, 7.0, 7.5, 8.0, 9.0, 10.0, 10.4):
    ps = outline(base, y)
    print(f"корпус y={y}: контуров {len(ps)}, площади {[round(p.area) for p in ps[:3]]}")

for y in (0.5, 1.5, 2.5, 3.0):
    ps = outline(cover, y)
    print(f"крышка y={y}: контуров {len(ps)}, площади {[round(p.area) for p in ps[:4]]}, габарит первого {np.round(ps[0].bounds, 2)}")

# Внутренняя граница тонкого бортика корпуса (над ступенькой) и внешняя граница выступа крышки
rim = outline(base, 9.5)
print("корпус y=9.5 bounds", [np.round(p.bounds, 2) for p in rim[:2]])
lip = outline(cover, 2.0)
print("крышка y=2.0 bounds", [np.round(p.bounds, 2) for p in lip[:2]])
