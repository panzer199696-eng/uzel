"""Поиск скрытых щелей: режем деталь по Z как слайсер и ищем контуры-нитки (площадь/периметр < порога).

Щель в микроны между добавленным и исходным телом замкнутость не ломает, а слайсер на неудачном
слое превращает её в дыру. Код возврата 1 — найдены.
"""
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import print_file  # noqa: E402
from mesh_check import PARTS, load_parts  # noqa: E402

CASE_DIR = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
THIN = 0.03   # мм: средняя ширина контура, ниже которой это не стенка, а щель

found = 0
files = sys.argv[1:] or [print_file(part, "3mf") for part in PARTS]
for path, (part, m) in ((p, pm) for p in files for pm in load_parts(p)):
    name = f"{Path(path).name} / {part}"
    hits = []
    for z in np.arange(0.025, m.bounds[1][2], 0.05):
        sec = m.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
        if sec is None:
            continue
        for e in sec.discrete:
            p = Polygon(e[:, :2])
            if p.length > 0.5 and 2 * abs(p.area) / p.length < THIN:
                hits.append((round(z, 3), round(p.length, 2), np.round(np.array(p.centroid.coords[0]), 1)))
    found += len(hits)
    zs = sorted({h[0] for h in hits})
    print(f"{name}: щелей {len(hits)}, слои z: {zs[:3]}…{zs[-3:]}" if hits else f"{name}: щелей 0")
    for h in hits[:10]:
        print(f"   z={h[0]} длина контура {h[1]} мм, центр {h[2]}")
sys.exit(1 if found else 0)
