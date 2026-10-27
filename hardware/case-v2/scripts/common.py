"""Общее для скриптов корпуса v2: загрузка сеток, перевод в manifold и обратно."""
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
import manifold3d as mf
import numpy as np
import trimesh

S = str(CASE_DIR / "omi_triangle_v1")
OUT = str(CASE_DIR / "out")
VERSION = "2.4"   # растёт с каждой выгрузкой под печать; в имени файлов и в 3MF


def print_file(part, ext):
    """Файл под печать: part — plate (весь стол) и т. п."""
    return str(CASE_DIR / "out" / f"uzel_case_v{VERSION}_{part}.{ext}")


def export_plate(objects, path):
    """Весь стол одним 3MF. objects — [(имя детали, [(trimesh, имя части)], (dx, dy))].
    Деталь из нескольких частей — объект из компонентов: слайсер видит одну деталь, частям можно
    назначить разные прутки. Индексы вершин сохраняются (см. export_3mf)."""
    res, items, oid = [], [], 0
    for name, parts, (dx, dy) in objects:
        ids = []
        for m, part in parts:
            oid += 1
            ids.append(oid)
            verts = "".join(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in m.vertices)
            tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in m.faces)
            res.append(f'<object id="{oid}" type="model" name="{part}"><mesh><vertices>{verts}</vertices>'
                       f'<triangles>{tris}</triangles></mesh></object>')
        if len(ids) > 1:
            oid += 1
            comps = "".join(f'<component objectid="{i}"/>' for i in ids)
            res.append(f'<object id="{oid}" type="model" name="{name}"><components>{comps}</components></object>')
            top = oid
        else:
            top = ids[0]
        items.append(f'<item objectid="{top}" transform="1 0 0 0 1 0 0 0 1 {dx:.3f} {dy:.3f} 0"/>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="ru-RU" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<metadata name="Title">УЗЕЛ v{VERSION}</metadata>'
             f'<resources>{"".join(res)}</resources><build>{"".join(items)}</build></model>')
    _write_3mf(path, model)


def _write_3mf(path, model):
    import zipfile
    types = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)


def load(name):
    m = trimesh.load(f"{S}\\{name}")
    m.merge_vertices(digits_vertex=3)
    m.update_faces(m.nondegenerate_faces())
    m.update_faces(m.unique_faces())
    return m


def to_mf(m):
    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32),
                               tri_verts=np.asarray(m.faces, np.uint32)))


def to_tm(man):
    mesh = man.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3], faces=np.asarray(mesh.tri_verts), process=False)


def export_3mf(parts, path, name):
    """3MF без lxml. parts — [(trimesh, имя части)]. В отличие от STL хранит индексы вершин: места, где
    тело касается себя по линии (manifold держит там раздельные вершины), остаются замкнутой сеткой.
    Несколько частей собираются в один объект из компонентов: слайсер (Creality Print / Orca) видит одну
    деталь из частей, и каждой части можно назначить свой пруток."""
    import zipfile
    objs = []
    for i, (m, part) in enumerate(parts, 1):
        verts = "".join(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in m.vertices)
        tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in m.faces)
        objs.append(f'<object id="{i}" type="model" name="{part}"><mesh><vertices>{verts}</vertices>'
                    f'<triangles>{tris}</triangles></mesh></object>')
    if len(parts) > 1:
        comps = "".join(f'<component objectid="{i}"/>' for i in range(1, len(parts) + 1))
        top = len(parts) + 1
        objs.append(f'<object id="{top}" type="model" name="{name}"><components>{comps}</components></object>')
    else:
        top = 1
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="ru-RU" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<resources>{"".join(objs)}</resources><build><item objectid="{top}"/></build></model>')
    types = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)


def main_body(man):
    """Самое объёмное тело (щепки нулевого объёма после булевых операций — отбросить)."""
    parts = man.decompose()
    return max(parts, key=lambda q: q.volume()), sum(q.volume() for q in parts) - max(q.volume() for q in parts)


def cover_transform(rot_axis, y_top, base_bounds, cover_bounds):
    """4×4: поворот крышки на 180° и совмещение с телом корпуса (без ушка, z от 3.5)."""
    if rot_axis == "z":
        R = trimesh.transformations.rotation_matrix(np.pi, [0, 0, 1])
    else:
        R = trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0])
    corners = trimesh.bounds.corners(cover_bounds)
    rc = trimesh.transform_points(corners, R)
    lo, hi = rc.min(0), rc.max(0)
    body_cx = (base_bounds[0][0] + base_bounds[1][0]) / 2
    body_cz = (3.5 + base_bounds[1][2]) / 2
    T = np.eye(4)
    T[:3, 3] = [body_cx - (lo[0] + hi[0]) / 2, y_top - hi[1], body_cz - (lo[2] + hi[2]) / 2]
    return T @ R
