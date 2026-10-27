"""Карта высот корпуса + упоры крышки, повёрнутые USB-стенкой вниз — для сверки с фото."""
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
import sys

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import ndimage

sys.stdout.reconfigure(encoding="utf-8")
C = str(CASE_DIR / "out")
Hb = np.load(f"{C}\\hm_base.npy")
fig, axes = plt.subplots(1, 3, figsize=(21, 7))
for ax, ang in zip(axes, (120, -120, 180)):
    img = ndimage.rotate(np.nan_to_num(Hb, nan=-1), ang, reshape=True, order=0, cval=-1)
    img = np.where(img < 0, np.nan, img)
    ax.imshow(img, origin="lower", cmap="turbo")
    ax.set_title(f"корпус, поворот {ang}° (смотрим в открытый корпус)")
    ax.axis("off")
plt.tight_layout(); plt.savefig(f"{C}\\rot_base.png", dpi=60)
