"""Срез лица крышки на глубине гравировки: эмблема должна быть контуром, а не сплошным пятном."""
import sys
from pathlib import Path

import matplotlib
import numpy as np
import trimesh
from shapely.geometry import Polygon

matplotlib.use("Agg")
import matplotlib.pyplot as plt

CASE_DIR = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import VERSION, print_file  # noqa: E402

files = [(CASE_DIR / "out" / "prev" / "uzel_case_v2_cover.stl", "v2 (дырки не вычитались)"),
         (print_file("cover", "stl"), f"v{VERSION}")]
fig, axes = plt.subplots(1, len(files), figsize=(12, 6))
for ax, (path, title) in zip(axes, files):
    m = trimesh.load(path)
    z = m.bounds[1][2] - 0.2          # лицо крышки на столе снизу? берём оба края и смотрим, где эмблема
    best = None
    for zz in (m.bounds[0][2] + 0.2, m.bounds[1][2] - 0.2):
        sec = m.section(plane_origin=[0, 0, zz], plane_normal=[0, 0, 1])
        if sec is None:
            continue
        polys = [Polygon(e[:, :2]) for e in sec.discrete]
        if best is None or len(polys) > len(best[1]):
            best = (zz, polys)
    zz, polys = best
    print(f"{title}: срез z={zz:.2f}, контуров {len(polys)}, объём {m.volume:.1f}")
    for p in polys:
        x, y = p.exterior.xy
        ax.plot(x, y, "-", lw=0.8)
    ax.set_aspect("equal"); ax.set_title(f"{title}: контуров {len(polys)}")
plt.tight_layout()
plt.savefig(CASE_DIR / "renders" / "emblem_section.png", dpi=80)
