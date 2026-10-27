"""Посадка крышки на корпус по STEP: какое преобразование даёт ~0 пересечения."""
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
import sys

from build123d import Axis, Location, Plane, Pos, Rot, import_step, Box

sys.stdout.reconfigure(encoding="utf-8")
SRC = str(CASE_DIR / "out" / "src")
base = import_step(f"{SRC}\\base.STEP")
cover = import_step(f"{SRC}\\top_cover.STEP")
bb, cb = base.bounding_box(), cover.bounding_box()
print("корпус", [round(v, 2) for v in (bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z)], "объём", round(base.volume, 1))
print("крышка", [round(v, 2) for v in (cb.min.X, cb.min.Y, cb.min.Z, cb.max.X, cb.max.Y, cb.max.Z)], "объём", round(cover.volume, 1))

# Крышка лежит плитой на верхнем бортике корпуса: её грань y=1 (низ плиты) совпадает с верхом бортика.
# Варианты: поворот 180° вокруг оси Z (зеркало X и Y) либо вокруг оси X (зеркало Y и Z).
results = []
for name, rot in (("вокруг Z", Rot(0, 0, 180)), ("вокруг X", Rot(180, 0, 0))):
    c = cover.moved(rot)
    c_bb = c.bounding_box()
    for dy_top in (bb.max.Y + 1.0, bb.max.Y + 0.8, bb.max.Y + 1.2):
        # совмещаем по X и Z центры габаритов тела корпуса без ушка (z от 3.5) и верх плиты на dy_top
        body_cx = (bb.min.X + bb.max.X) / 2
        body_cz = (3.5 + bb.max.Z) / 2
        dx = body_cx - (c_bb.min.X + c_bb.max.X) / 2
        dz = body_cz - (c_bb.min.Z + c_bb.max.Z) / 2
        dy = dy_top - c_bb.max.Y
        cc = c.moved(Pos(dx, dy, dz))
        inter = base & cc
        v = inter.volume if inter is not None else 0.0
        results.append((v, name, dy_top))
        print(f"{name}: верх крышки y={dy_top:.2f} → пересечение {v:.2f} мм³")
best = min(results)
print("лучше всего:", best)
