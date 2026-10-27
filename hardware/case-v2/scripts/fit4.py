"""Точная высота посадки и где касание; карта зазоров упоров до корпуса в правильной посадке."""
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, cover_transform, load, to_mf, to_tm  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
base, cover = load("base.stl"), load("top_cover.STL")
B = to_mf(base)
for y_top in np.arange(11.6, 12.21, 0.05):
    T = cover_transform("z", y_top, base.bounds, cover.bounds)
    c = cover.copy(); c.apply_transform(T)
    inter = B ^ to_mf(c)
    v = inter.volume()
    line = f"верх {y_top:.2f}: пересечение {v:6.2f}"
    if 0 < v < 20:
        t = to_tm(inter)
        parts = t.split(only_watertight=False)
        line += " | зоны: " + "; ".join(f"x{p.bounds[0][0]:.0f}-{p.bounds[1][0]:.0f} y{p.bounds[0][1]:.1f}-{p.bounds[1][1]:.1f} z{p.bounds[0][2]:.0f}-{p.bounds[1][2]:.0f}" for p in parts[:6])
    print(line)
T = cover_transform("z", 12.0, base.bounds, cover.bounds)
np.save(f"{OUT}\\cover_T.npy", T)
c = cover.copy(); c.apply_transform(T)
c.export(f"{OUT}\\cover_seated.stl")
print("крышка в посадке: bounds", np.round(c.bounds, 2).tolist())
