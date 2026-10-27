"""Посадка крышки: пересечение тел для двух вариантов поворота и разной высоты."""
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import cover_transform, load, to_mf  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
base, cover = load("base.stl"), load("top_cover.STL")
B = to_mf(base)
for axis in ("z", "x"):
    for y_top in np.arange(10.5, 16.01, 0.5):
        T = cover_transform(axis, y_top, base.bounds, cover.bounds)
        c = cover.copy(); c.apply_transform(T)
        v = (B ^ to_mf(c)).volume()
        print(f"поворот вокруг {axis}: верх крышки {y_top:5.2f} → пересечение {v:7.2f} мм³")
