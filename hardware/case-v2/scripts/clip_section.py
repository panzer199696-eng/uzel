"""Срез сборки через ось клипсы: язычок, зазор под ткань и зацеп видны в профиль."""
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np
import trimesh
from shapely.geometry import Polygon, box

matplotlib.use("Agg")
import matplotlib.pyplot as plt

CASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
UP = np.array([np.cos(np.radians(330)), np.sin(np.radians(330))])   # (x, z) — «вверх» при ношении
SIDE = np.array([-UP[1], UP[0]])

# та же опорная точка, что в build_v2.py: центр контура спинки без ушка
sec = load("base.stl").section(plane_origin=[0, 2.5, 0], plane_normal=[0, 1, 0])
back = max((Polygon(np.c_[e[:, 0], e[:, 2]]).buffer(0) for e in sec.discrete), key=lambda p: p.area)
origin = np.array(back.difference(box(-10, -10, 60, 3.55)).centroid.coords[0])

rep = json.loads((CASE_DIR / "out" / "build_report.json").read_text(encoding="utf-8"))
s_block = rep["s_end"] - 3.5                       # середина колодки клипсы
fig = plt.figure(figsize=(16, 10))
ax_long = fig.add_subplot(2, 1, 1)
ax_blk, ax_tng = fig.add_subplot(2, 2, 3), fig.add_subplot(2, 2, 4)
colors = {"v2_base_model.stl": "#222", "v2_cover_seated.stl": "#2a6b66", "v2_clip_seated.stl": "#c0392b"}
cuts = ((ax_long, origin, SIDE, UP), (ax_blk, origin + s_block * UP, UP, SIDE), (ax_tng, origin, UP, SIDE))
for name, col in colors.items():
    m = trimesh.load(CASE_DIR / "out" / name)
    for ax, at, normal, along in cuts:
        s = m.section(plane_origin=[at[0], 0, at[1]], plane_normal=[normal[0], 0, normal[1]])
        if s is None:
            continue
        for e in s.discrete:
            u = (e[:, [0, 2]] - origin) @ along
            ax.fill(u, e[:, 1], color=col, alpha=0.35, lw=0)
            ax.plot(u, e[:, 1], "-", color=col, lw=0.8)
ax_long.set_title("Вдоль клипсы (слева — низ при ношении, USB; справа — верх). Красное — съёмная клипса")
ax_long.set_xlabel("вдоль кулона, мм (0 — центр спинки)")
ax_long.set_ylabel("толщина, мм (0 — бывшая спинка Omi)")
ax_blk.set_title("Поперёк через колодку: «ласточкин хвост»")
ax_tng.set_title("Поперёк через язычок: зазор под ткань")
for ax in (ax_blk, ax_tng):
    ax.set_xlim(-16, 16); ax.set_ylim(-4, 3); ax.set_xlabel("поперёк, мм")
for ax in (ax_long, ax_blk, ax_tng):
    ax.set_aspect("equal"); ax.grid(alpha=0.3)
plt.tight_layout()
out = CASE_DIR / "renders" / "v2_clip_section.png"
plt.savefig(out, dpi=90)
print(out)
