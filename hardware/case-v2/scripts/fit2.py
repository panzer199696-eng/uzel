"""Зазоры упоров крышки до корпуса: две гипотезы посадки (с зеркалом по X и без)."""
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
import sys

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.stdout.reconfigure(encoding="utf-8")
C = str(CASE_DIR / "out")
Hb = np.load(f"{C}\\hm_base.npy")    # max Y корпуса, сетка xs=lo-1.. шаг 0.25
Hc = np.load(f"{C}\\hm_cover.npy")   # max Y крышки
step = 0.25
# сетки как в heightmap.py
bx0, bz0 = 0.02 - 1, 0.0 - 1
cx0, cz0 = 0.01 - 1, 0.0 - 1
YTOP = 11.5  # наружная грань крышки, если плита (1 мм) лежит на бортике 10.5


def cover_in_base(mirror):
    """Нижняя точка крышки (y в системе корпуса) на сетке корпуса."""
    out = np.full(Hb.shape, np.nan)
    nz, nx = Hb.shape
    for j in range(nz):
        zb = bz0 + j * step
        zc = zb - 3.5
        jc = int(round((zc - cz0) / step))
        if not (0 <= jc < Hc.shape[0]):
            continue
        for i in range(nx):
            xb = bx0 + i * step
            xc = (43.66 - xb) if mirror else xb
            ic = int(round((xc - cx0) / step))
            if 0 <= ic < Hc.shape[1] and not np.isnan(Hc[jc, ic]):
                out[j, i] = YTOP - Hc[jc, ic]
    return out


fig, axes = plt.subplots(1, 2, figsize=(20, 9))
for ax, mirror in zip(axes, (False, True)):
    low = cover_in_base(mirror)
    gap = low - Hb  # зазор от низа крышки до поверхности корпуса под ней
    post = (low < 10.0)  # только упоры (ниже плиты)
    g = np.where(post, gap, np.nan)
    inter = np.nansum(np.clip(-g, 0, None)) * step * step
    print(f"зеркало={mirror}: объём пересечений упоров с корпусом {inter:.1f} мм³, мин. зазор {np.nanmin(g):.2f}")
    im = ax.imshow(g, origin="lower", extent=(bx0, bx0 + Hb.shape[1] * step, bz0, bz0 + Hb.shape[0] * step),
                   cmap="RdYlGn", vmin=-2, vmax=6)
    ax.contour(np.arange(Hb.shape[1]) * step + bx0, np.arange(Hb.shape[0]) * step + bz0, np.nan_to_num(Hb, nan=0),
               levels=[3, 6.5, 9], colors="k", linewidths=0.5)
    ax.set_title(f"зазор упор→корпус, мм (зеркало X: {mirror}); чёрное — рельеф корпуса")
    ax.set_xticks(np.arange(0, 46, 2)); ax.set_yticks(np.arange(0, 44, 2)); ax.grid(alpha=0.3)
    plt.colorbar(im, ax=ax)
    np.save(f"{C}\\cover_low_m{int(mirror)}.npy", low)
plt.tight_layout(); plt.savefig(f"{C}\\fit_gap.png", dpi=65)
