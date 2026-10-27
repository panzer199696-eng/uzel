"""Проверка файлов под печать: 3MF — по индексам вершин (как читает слайсер), STL — со склейкой по координатам.

Без аргументов проверяет стол текущей версии (common.VERSION): корпус, крышку, эмблему, клипсу.
Каждая часть 3MF проверяется отдельно. Код возврата 1 — есть брак.
"""
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import print_file  # noqa: E402

CASE_DIR = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
PARTS = ("plate",)   # весь стол одним файлом; части проверяются по отдельности


def read_3mf(path):
    """[(имя части, trimesh)] — по объекту с сеткой; объекты-сборки из компонентов пропускаются."""
    xml = zipfile.ZipFile(path).read("3D/3dmodel.model").decode("utf-8")
    out = []
    for name, body in re.findall(r'<object id="\d+" type="model" name="([^"]*)"><mesh>(.*?)</mesh>', xml, re.S):
        v = np.array(re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', body), float)
        f = np.array(re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', body), int)
        out.append((name, trimesh.Trimesh(v, f, process=False)))
    return out


def load_parts(path):
    if str(path).lower().endswith(".3mf"):
        return read_3mf(path)
    m = trimesh.load(path)
    m.merge_vertices(digits_vertex=4)
    return [(Path(path).stem, m)]


def main():
    files = sys.argv[1:] or [print_file(part, "3mf") for part in PARTS]
    bad = 0
    for f in files:
        for part, m in load_parts(f):
            _, counts = np.unique(np.sort(m.edges, axis=1), axis=0, return_counts=True)
            open_e, nonman = int((counts == 1).sum()), int((counts > 2).sum())
            bodies = len(m.split(only_watertight=False))
            ok = (m.is_watertight and m.is_winding_consistent and m.volume > 0 and bodies == 1
                  and open_e == 0 and nonman == 0)
            bad += not ok
            print(f"{Path(f).name} / {part}: {'OK' if ok else 'БРАК'} — замкнута {m.is_watertight}, тел {bodies}, "
                  f"открытых рёбер {open_e}, рёбер на >2 грани {nonman}, объём {m.volume:.1f} мм³, "
                  f"габарит {np.round(m.extents, 2)}, низ z={m.bounds[0][2]:.3f}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
