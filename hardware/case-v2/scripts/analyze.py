from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
import sys
import numpy as np
import trimesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.stdout.reconfigure(encoding="utf-8")
SRC = str(CASE_DIR / "omi_triangle_v1")
OUT = str(CASE_DIR / "out")

for name in ("base.stl", "top_cover.STL"):
    m = trimesh.load(f"{SRC}\\{name}")
    print(f"== {name}: вершин {len(m.vertices)}, граней {len(m.faces)}, watertight {m.is_watertight}, объём {m.volume/1000:.2f} см3")
    print("   bounds", np.round(m.bounds, 2).tolist(), "размер", np.round(m.extents, 2).tolist())

base = trimesh.load(f"{SRC}\\base.stl")
cover = trimesh.load(f"{SRC}\\top_cover.STL")

# срезы base по высоте (ось с размером 10.5 — толщина)
ax_thick = int(np.argmin(base.extents))
print("ось толщины base:", "xyz"[ax_thick])
lo, hi = base.bounds[0][ax_thick], base.bounds[1][ax_thick]
levels = np.linspace(lo + 0.3, hi - 0.3, 8)
fig, axes = plt.subplots(2, 4, figsize=(16, 8))
normal = np.zeros(3); normal[ax_thick] = 1
others = [i for i in range(3) if i != ax_thick]
for a, z in zip(axes.flat, levels):
    origin = np.zeros(3); origin[ax_thick] = z
    sec = base.section(plane_origin=origin, plane_normal=normal)
    if sec is not None:
        for ent in sec.discrete:
            a.plot(ent[:, others[0]], ent[:, others[1]], "k-", lw=0.7)
    a.set_title(f"{'xyz'[ax_thick]} = {z:.2f}")
    a.set_aspect("equal"); a.grid(alpha=0.3)
plt.tight_layout(); plt.savefig(f"{OUT}\\base_slices.png", dpi=80)

# крышка: срезы
ax_c = int(np.argmin(cover.extents))
lo, hi = cover.bounds[0][ax_c], cover.bounds[1][ax_c]
print("крышка: ось толщины", "xyz"[ax_c], "от", round(lo, 2), "до", round(hi, 2))
fig, axes = plt.subplots(1, 4, figsize=(16, 4))
normal = np.zeros(3); normal[ax_c] = 1
others = [i for i in range(3) if i != ax_c]
for a, z in zip(axes.flat, np.linspace(lo + 0.2, hi - 0.2, 4)):
    origin = np.zeros(3); origin[ax_c] = z
    sec = cover.section(plane_origin=origin, plane_normal=normal)
    if sec is not None:
        for ent in sec.discrete:
            a.plot(ent[:, others[0]], ent[:, others[1]], "k-", lw=0.7)
    a.set_title(f"{'xyz'[ax_c]} = {z:.2f}"); a.set_aspect("equal"); a.grid(alpha=0.3)
plt.tight_layout(); plt.savefig(f"{OUT}\\cover_slices.png", dpi=80)
print("ok")
