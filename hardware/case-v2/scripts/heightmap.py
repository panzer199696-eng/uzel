"""Карта высот: для сетки X–Z первая поверхность при взгляде вдоль -Y (со стороны крышки)."""
import sys

import matplotlib
import numpy as np
import trimesh

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.stdout.reconfigure(encoding="utf-8")
path, out, from_top = sys.argv[1], sys.argv[2], sys.argv[3] == "top"
m = trimesh.load(path)
lo, hi = m.bounds
step = 0.25
xs = np.arange(lo[0] - 1, hi[0] + 1, step)
zs = np.arange(lo[2] - 1, hi[2] + 1, step)
X, Z = np.meshgrid(xs, zs)
# z-буфер: растеризуем треугольники в плоскость X-Z, берём max Y (вид сверху) или min Y (снизу)
H = np.full(X.shape, np.nan)
tri = m.vertices[m.faces]
for t in tri:
    x0, z0 = t[:, 0], t[:, 2]
    i0 = max(int((x0.min() - xs[0]) / step), 0); i1 = min(int((x0.max() - xs[0]) / step) + 1, len(xs) - 1)
    j0 = max(int((z0.min() - zs[0]) / step), 0); j1 = min(int((z0.max() - zs[0]) / step) + 1, len(zs) - 1)
    if i1 < i0 or j1 < j0:
        continue
    gx, gz = np.meshgrid(xs[i0:i1 + 1], zs[j0:j1 + 1])
    (ax_, az_), (bx, bz), (cx, cz) = (t[0, 0], t[0, 2]), (t[1, 0], t[1, 2]), (t[2, 0], t[2, 2])
    den = (bz - cz) * (ax_ - cx) + (cx - bx) * (az_ - cz)
    if abs(den) < 1e-12:
        continue
    w1 = ((bz - cz) * (gx - cx) + (cx - bx) * (gz - cz)) / den
    w2 = ((cz - az_) * (gx - cx) + (ax_ - cx) * (gz - cz)) / den
    w3 = 1 - w1 - w2
    inside = (w1 >= -1e-6) & (w2 >= -1e-6) & (w3 >= -1e-6)
    if not inside.any():
        continue
    yv = w1 * t[0, 1] + w2 * t[1, 1] + w3 * t[2, 1]
    sub = H[j0:j1 + 1, i0:i1 + 1]
    cur = np.where(np.isnan(sub), -np.inf if from_top else np.inf, sub)
    newv = np.maximum(cur, np.where(inside, yv, -np.inf)) if from_top else np.minimum(cur, np.where(inside, yv, np.inf))
    H[j0:j1 + 1, i0:i1 + 1] = np.where(np.isfinite(newv), newv, np.nan)
fig, ax = plt.subplots(figsize=(11, 10))
im = ax.imshow(H, origin="lower", extent=(xs[0], xs[-1], zs[0], zs[-1]), cmap="turbo")
cs = ax.contour(X, Z, np.nan_to_num(H, nan=-1), levels=np.arange(0, 12, 1), colors="k", linewidths=0.3)
plt.colorbar(im, label="Y, мм (высота поверхности)")
ax.set_xlabel("X, мм"); ax.set_ylabel("Z, мм"); ax.grid(alpha=0.3)
ax.set_xticks(np.arange(0, 46, 2)); ax.set_yticks(np.arange(0, 44, 2))
ax.set_title(f"{path.split(chr(92))[-1]}: {'вид со стороны крышки' if from_top else 'вид со стороны дна'}")
plt.savefig(out, dpi=75, bbox_inches="tight")
np.save(out.replace(".png", ".npy"), H)
print("ok", H.shape)
