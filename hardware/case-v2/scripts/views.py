"""Ортогональные виды STL с осями (matplotlib) — понять, где ушко, USB, выключатель."""
import sys

import matplotlib
import numpy as np
import trimesh

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

sys.stdout.reconfigure(encoding="utf-8")
path, out = sys.argv[1], sys.argv[2]
m = trimesh.load(path)
tri = m.vertices[m.faces]
n = m.face_normals
light = np.array([0.3, -0.5, 0.8]); light /= np.linalg.norm(light)

views = [("спереди (смотрим вдоль +Y, со стороны дна)", 90, -90), ("сзади (вдоль -Y, со стороны крышки)", -90, 90),
         ("изометрия 1", 25, -60), ("изометрия 2", 25, 120), ("сверху по Z", 90, 0), ("снизу по Z", -90, 0)]
fig = plt.figure(figsize=(18, 11))
for i, (title, elev, azim) in enumerate(views, 1):
    ax = fig.add_subplot(2, 3, i, projection="3d")
    shade = 0.35 + 0.65 * np.clip(n @ light, 0, 1)
    col = np.c_[shade * 0.9, shade * 0.55, shade * 0.25, np.ones(len(shade))]
    pc = Poly3DCollection(tri, facecolors=col, edgecolors="none")
    ax.add_collection3d(pc)
    lo, hi = m.bounds
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect(hi - lo)
    ax.view_init(elev=elev, azim=azim)
    ax.set_xlabel("X"); ax.set_ylabel("Y"); ax.set_zlabel("Z")
    ax.set_title(title, fontsize=10)
plt.tight_layout()
plt.savefig(out, dpi=70)
print("ok")
