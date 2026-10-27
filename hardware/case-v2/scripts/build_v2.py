"""Корпус УЗЛА v2 на базе Omi Triangle v1: съёмная клипса в кармане «ласточкин хвост», защёлка крышки,
поджатые упоры, эмблема-вкладыш другого цвета. Печать — PLA (от этого толщина язычка и высота защёлок).

Система координат исходных STL: X — ширина, Y — толщина (Y=0 — спинка корпуса),
Z — высота. USB и выключатель выходят в левую верхнюю стенку; ношение «USB вниз»
значит: вниз = внешняя нормаль этой стенки (150°), вверх u = (cos330°, sin330°).
"""
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
import json
import os
import sys

import manifold3d as mf
import numpy as np
import trimesh
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
import texture  # noqa: E402
from common import OUT, export_plate, load, main_body, print_file, to_mf, to_tm  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

# ---------------- параметры ----------------
PLATE_T = 3.1        # толщина добавленной спинки
TONGUE_T = 1.6       # толщина язычка: печать PLA (предел ~1.5 %, у PETG было 2.2); деформация ∝ толщине
SLOT_UP = 0.05       # прорезь заходит в дно Omi: иначе его дно (y 0…0.003) и спинка дают щель в микроны
PLATE_OVERLAP = 0.3  # спинка заходит в дно Omi по той же причине
GAP = PLATE_T - TONGUE_T + SLOT_UP  # 0.95 — зазор под ткань в покое
TONGUE_W = 16.0      # ширина язычка: 18 у отдельной клипсы даёт деформацию 3 % (колодка съедает длину), 16 — 2.1 %
SIDE_SLOT = 0.6      # зазор по бокам язычка в кармане (отдельная деталь — хватает меньшего, чем у прорези)
DT_ANGLE = 15.0      # наклон стенок «ласточкина хвоста» от вертикали, °
DT_CLR = 0.15        # зазор колодки клипсы в кармане по каждой стороне
DT_FLOOR = 0.15      # зазор колодки до дна кармана
TAPER = 0.25         # сужение кармана на сторону в конце хода → натяг TAPER − DT_CLR = 0.1
TAPER_L = 3.0        # длина клинового участка
ROOT_L = 7.0         # длина колодки клипсы вдоль оси
WALL = 1.4           # стенка спинки сбоку от кармана (у дна)
BUMP_H = 1.1         # зацеп на конце язычка (остаётся 0.45 до дна кармана)
PRESS_ADD = 0.4      # на сколько удлинить прижимные упоры крышки
TEXTURE = os.environ.get("UZEL_TEXTURE", "hex")   # фактура боковин: tri (low-poly) | hex (соты)
SNAP_H = 0.2         # бугорки защёлки на бортике (PLA: юбка крышки хрупкая, у PETG было 0.3)
UP = np.array([np.cos(np.radians(330)), np.sin(np.radians(330))])  # (x, z)
SIDE = np.array([-UP[1], UP[0]])

base = load("base.stl")
cover = load("top_cover.STL")
T = np.load(f"{OUT}\\cover_T.npy")
cover_seated = cover.copy(); cover_seated.apply_transform(T)
B = to_mf(base)
report = {}


def section_poly(mesh, y, keep="largest"):
    sec = mesh.section(plane_origin=[0, y, 0], plane_normal=[0, 1, 0])
    polys = [Polygon(np.c_[e[:, 0], e[:, 2]]).buffer(0) for e in sec.discrete]
    polys = [p for p in polys if p.area > 1]
    polys.sort(key=lambda p: -p.area)
    return polys


def prism_xz(poly, y0, y1):
    """Многоугольник в плоскости X–Z, выдавленный по Y от y0 до y1."""
    from shapely.geometry.polygon import orient
    poly = orient(poly.buffer(0), sign=1.0)
    xs, zs = poly.exterior.coords.xy
    cs = mf.CrossSection([np.c_[np.asarray(xs)[:-1], np.asarray(zs)[:-1]]])
    for hole in poly.interiors:
        # у shapely дырки идут по часовой — CrossSection счёл бы их пустыми и ничего не вычел
        hx, hz = orient(Polygon(hole), sign=1.0).exterior.coords.xy
        cs = cs - mf.CrossSection([np.c_[np.asarray(hx)[:-1], np.asarray(hz)[:-1]]])
    ex = mf.Manifold.extrude(cs, y1 - y0)  # вдоль +Z манифолда
    # поворот на +90° вокруг X: (x, cs_y, h) -> (x, -h, cs_y), затем сдвиг верха на y1
    return ex.rotate([90, 0, 0]).translate([0, y1, 0])


def su_to_xz(s, t, origin):
    p = origin + s * UP + t * SIDE
    return p


def rect_su(s0, s1, t0, t1, origin):
    pts = [su_to_xz(s, t, origin) for s, t in ((s0, t0), (s1, t0), (s1, t1), (s0, t1))]
    return Polygon(pts)


# ---------------- 1. спинка ----------------
back = section_poly(base, 2.5)[0]   # полный контур: скругление края спинки здесь уже кончилось
body = back.difference(box(-10, -10, 60, 3.55))  # без ушка: отверстие под шнурок остаётся открытым
plate_full = prism_xz(body, -PLATE_T + 0.8, PLATE_OVERLAP)
plate_chamf = prism_xz(body.buffer(-0.8, join_style=1), -PLATE_T, -PLATE_T + 0.81)
plate = (plate_full + plate_chamf).hull()
# скругление края спинки заполняем только кольцом по стенке, внутрь полости не лезем
secs = section_poly(base, 1.3)   # [наружный контур, контур полости, ...]
inner = secs[1]
filler = prism_xz(body, -0.01, 2.5) - prism_xz(inner.buffer(0.05), -0.5, 3.0)
plate = plate + filler
report["полость_у_дна_площадь"] = round(inner.area, 1)
report["спинка_габарит"] = [round(v, 2) for v in body.bounds]

# ---------------- 2. клипса — отдельная деталь в кармане «ласточкин хвост» ----------------
# Карман в спинке вдоль оси «вверх», открыт снизу (сторона USB), закрыт сверху. Клипса — колодка
# с трапециевидным профилем (держит от вырывания) + язычок, который лежит в том же кармане заподлицо
# со спинкой и отгибается наружу. Колодка вдвигается снизу до упора в закрытый конец; вес кулона
# при ношении прижимает её к упору, от выпадания — клиновой натяг на последних TAPER_L мм хода.
origin = np.array(body.centroid.coords[0])
# профиль ширины спинки вдоль оси «вверх»
s_vals = np.arange(-30, 30, 0.25)
widths = []
for s in s_vals:
    line = rect_su(s - 0.01, s + 0.01, -40, 40, origin)
    seg = body.intersection(line)
    widths.append(0 if seg.is_empty else seg.length / 2 - 0.02)
widths = np.array(widths)
inside = s_vals[widths > 0]
s_bot, s_top = inside.min(), inside.max()

TAN = np.tan(np.radians(DT_ANGLE))
hs = TONGUE_W / 2 + SIDE_SLOT                       # полуширина кармана на поверхности спинки
DT_Y0, DT_Y1 = -PLATE_T, SLOT_UP                    # ласточкин хвост: от поверхности до дна Omi
hf = hs + (DT_Y1 - DT_Y0) * TAN                     # полуширина у дна кармана
need = 2 * hf + 2 * WALL
# закрытый конец кармана — самое высокое место, где на всей длине колодки хватает ширины
ok = [s for s in s_vals if all(widths[(s_vals >= s - ROOT_L) & (s_vals <= s)] >= need)]
s_end = max(ok)
s_tip = s_bot + 3.0
L = (s_end - ROOT_L) - s_tip                       # свободная длина язычка
# деформация у корня консоли при отгибе на (ткань + зацеп − зазор + 0.6 на заход)
strain = {f: round(100 * 3 * TONGUE_T * (f + BUMP_H - GAP + 0.6) / (2 * L * L), 2) for f in (1, 2, 3)}
report.update(s_bot=round(s_bot, 2), s_top=round(s_top, 2), s_end=round(float(s_end), 2), tongue_len=round(L, 2),
              карман_ширина_у_дна=round(2 * hf, 2), ширина_спинки_нужна=round(need, 2),
              деформация_pct_ткань_1_2_3мм=list(strain.values()))


def dovetail(s0, s1, h0, h1, y0, y1):
    """Тело с трапециевидным сечением вдоль оси «вверх»: s0..s1; полуширина на y0 — h0 (в начале)
    и h1 (в конце), к y1 расширяется на (y1-y0)·tg угла. Выпуклое — строится оболочкой точек."""
    pts = []
    for s, h in ((s0, h0), (s1, h1)):
        for k in (-1, 1):
            for y, hh in ((y0, h), (y1, h + (y1 - y0) * TAN)):
                x, z = su_to_xz(s, k * hh, origin)
                pts.append((x, y, z))
    return mf.Manifold.hull_points(np.array(pts, dtype=np.float64))


# карман: прямой вырез над поверхностью спинки + ласточкин хвост; последние TAPER_L мм сужаются
s_open = s_bot - 3
pocket = (prism_xz(rect_su(s_open, s_end, -hs, hs, origin), -PLATE_T - 1, -PLATE_T + 0.05)
          + dovetail(s_open, s_end - TAPER_L, hs, hs, DT_Y0, DT_Y1)
          + dovetail(s_end - TAPER_L, s_end, hs, hs - TAPER, DT_Y0, DT_Y1))
# колодка клипсы: профиль кармана минус зазор по бокам и до дна; без сужения — отсюда натяг в конце хода
blk_y1 = DT_Y1 - DT_FLOOR
block = dovetail(s_end - ROOT_L, s_end, hs - DT_CLR, hs - DT_CLR, DT_Y0, blk_y1)
half = TONGUE_W / 2
tongue = prism_xz(rect_su(s_tip, s_end - ROOT_L + 0.5, -half, half, origin), -PLATE_T, -PLATE_T + TONGUE_T)
report["натяг_клина_на_сторону_мм"] = round(TAPER - DT_CLR, 2)
# зацеп: поперечный гребень на внутренней стороне язычка у конца, с пологим заходом снизу (выпуклое тело)
y0 = -PLATE_T + TONGUE_T - 0.05
prof = [(s_tip + 0.3, 0.0), (s_tip + 1.8, BUMP_H), (s_tip + 2.6, BUMP_H), (s_tip + 3.4, 0.0)]
pts = []
for sv, h in prof:
    for tv in (-half + 1, half - 1):
        x, z = su_to_xz(sv, tv, origin)
        pts += [(x, y0, z), (x, y0 + 0.05 + h, z)]
bump = mf.Manifold.hull_points(np.array(pts, dtype=np.float64))

# ---------------- 3. защёлка: бугорки на бортике ----------------
rim = section_poly(base, 9.6)[0]
rim_ext = Polygon(rim.exterior.coords)
report["бортик_снаружи"] = [round(v, 2) for v in rim_ext.bounds]
# середины трёх длинных сторон: точки бортика, наиболее удалённые от центра по направлениям 270, 150, 30
center = np.array(rim_ext.centroid.coords[0])
snaps_base, snaps_cut = [], []
for ang in (270, 150, 30):
    d = np.array([np.cos(np.radians(ang)), np.sin(np.radians(ang))])
    ring = rim_ext.exterior
    samp = np.array([ring.interpolate(t).coords[0] for t in np.arange(0, ring.length, 0.2)])
    rel = samp - center
    lateral = np.abs(rel[:, 0] * d[1] - rel[:, 1] * d[0])
    score = rel @ d - 5 * lateral
    p = samp[int(np.argmax(score))]
    tan = np.array([-d[1], d[0]])
    for y0, y1, out in ((9.35, 10.35, SNAP_H),):
        pass
    # профиль бугорка по высоте: заход сверху пологий, низ крутой; выпуклое тело
    prof = [(10.40, 0.0), (9.75, SNAP_H), (9.55, SNAP_H), (9.35, 0.0)]
    pb, pc = [], []
    for y, o in prof:
        for k in (-1, 1):
            pb += [tuple(np.r_[p + tan * 3.0 * k - d * 0.2, 0][[0, 2, 1]] + np.array([0, y, 0])),
                   tuple(np.r_[p + tan * 3.0 * k + d * o, 0][[0, 2, 1]] + np.array([0, y, 0]))]
            pc += [tuple(np.r_[p + tan * 3.3 * k - d * 0.2, 0][[0, 2, 1]] + np.array([0, y + 0.1 * np.sign(y - 9.9), 0])),
                   tuple(np.r_[p + tan * 3.3 * k + d * (o + 0.12), 0][[0, 2, 1]] + np.array([0, y + 0.1 * np.sign(y - 9.9), 0]))]
    snaps_base.append(mf.Manifold.hull_points(np.array(pb, dtype=np.float64)))
    snaps_cut.append(mf.Manifold.hull_points(np.array(pc, dtype=np.float64)))
snap_base = mf.Manifold.batch_boolean(snaps_base, mf.OpType.Add)
snap_cut = mf.Manifold.batch_boolean(snaps_cut, mf.OpType.Add)

# ---------------- сборка корпуса ----------------
base_v2 = ((B + plate) - pocket) + snap_base
report["корпус_v2_тел"] = len(to_tm(base_v2).split(only_watertight=False))
clip = block + tongue + bump
report["клипса_тел"] = len(clip.decompose())
report["клипса_в_корпус_мм3"] = round((clip ^ base_v2).volume(), 3)   # только клин натяга
report["клипса_в_корпус_без_клина_мм3"] = round((clip ^ ((B + plate) - pocket
                                                       - dovetail(s_end - TAPER_L, s_end, hs, hs, DT_Y0, DT_Y1))).volume(), 3)

# ---------------- 4. прижимные упоры крышки ----------------
hm = np.load(f"{OUT}\\hm_base.npy")
hx0, hz0, hstep = base.bounds[0][0] - 1, base.bounds[0][2] - 1, 0.25


def base_height(x, z):
    i, j = int(round((x - hx0) / hstep)), int(round((z - hz0) / hstep))
    win = hm[max(j - 2, 0):j + 3, max(i - 2, 0):i + 3]
    return np.nanmax(win) if np.isfinite(win).any() else np.nan


cs = cover_seated
down = cs.face_normals[:, 1] < -0.3          # грани, смотрящие в корпус
cent = cs.triangles_center
outline_body = Polygon(back.exterior.coords).difference(box(-10, -10, 60, 3.55))
press_faces, pillar_faces = [], []
for fi in np.where(down)[0]:
    x, y, z = cent[fi]
    if y > 10.9:                              # плита крышки и юбка — не упоры
        continue
    if outline_body.exterior.distance(Point(x, z)) < 2.2:
        continue                              # юбка и её выступы у края
    hb = base_height(x, z)
    gap = y - hb
    if np.isnan(gap):
        continue
    (press_faces if gap > 1.5 else pillar_faces).append(fi)
# Удлинение = копия крышки, сдвинутая к корпусу, внутри следа упоров. Призмы из каждого треугольника
# (прошлый вариант) на скатах почти касались соседних крутых стенок и давали щели в сотые доли мм.
# След ужат на 0.05: его стенки идут сквозь материал упора, а не по рёбрам сетки.
C = to_mf(cs)
footprint = unary_union([Polygon(cs.triangles[fi][:, [0, 2]]).buffer(0.001) for fi in press_faces])
footprint = footprint.buffer(-0.05, join_style=2)
foot_parts = list(getattr(footprint, "geoms", [footprint]))
press_ext = mf.Manifold.batch_boolean(
    [C.translate([0, -PRESS_ADD, 0]) ^ prism_xz(p, 5.0, 10.9) for p in foot_parts if p.area > 0.05], mf.OpType.Add)
press_ext = press_ext - B  # удлинение не должно входить в тело корпуса
report["упоры_граней_удлинено"] = len(press_faces)
report["упоры_пятен"] = len(foot_parts)
report["колонны_граней_не_тронуто"] = len(pillar_faces)
cover_v2_seated = (C + press_ext) - snap_cut

# ---------------- 5. эмблема на лицо крышки ----------------
from emblem import fit  # noqa: E402

EMBLEM_DEPTH = 0.4
face = section_poly(to_tm(cover_v2_seated), 11.9)
panel = Polygon(face[2].exterior.coords)             # внутренняя граница канавки треугольной панели
holes = [p for p in face if p.area < 5]
res = fit(panel, holes)
if res:
    size, centre, emb, clearance = res
    report.update(эмблема_ширина_мм=float(size), эмблема_центр=[round(float(v), 2) for v in centre],
                  эмблема_запас_мм=round(float(clearance), 2))
    # вкладыш другого цвета: гнездо в крышке и ответная часть ровно по нему (лицо крышки — плоскость y=12)
    cover_v2_seated = cover_v2_seated - prism_xz(emb, 12.0 - EMBLEM_DEPTH, 12.5)
    emblem_seated = prism_xz(emb, 12.0 - EMBLEM_DEPTH, 12.0)
    # самая узкая перемычка эмблемы: при ужатии на половину сопла (0.2) она не должна распадаться
    report["эмблема_частей"] = len(getattr(emb, "geoms", [emb]))
    thin = emb.buffer(-0.2)
    report["эмблема_частей_после_ужатия_0.2"] = len(getattr(thin, "geoms", [thin]))
else:
    report["эмблема"] = "не влезла"
    emblem_seated = None

# ---------------- проверки ----------------
inter_orig = (B ^ C).volume()
inter_new = (base_v2 ^ cover_v2_seated).volume()
press_vs_base = (press_ext ^ B).volume()
report.update(пересечение_исходное=round(inter_orig, 2), пересечение_v2=round(inter_new, 2),
              удлинение_упоров_в_корпус=round(press_vs_base, 3),
              объём_корпуса=round(B.volume(), 1), объём_корпуса_v2=round(base_v2.volume(), 1),
              объём_крышки=round(C.volume(), 1), объём_крышки_v2=round(cover_v2_seated.volume(), 1))

# ---------------- 6. фактура «под кожу» на видимых боковых стенках ----------------
# Корпус — от фаски спинки до низа юбки крышки; крышка — её наружная стенка ниже скругления лица.
# Контур крышки берётся по сплошной плите (y=10.8): ниже юбка разбита на куски, и контур куска
# захватил бы внутреннюю грань юбки — рельеф ушёл бы в бортик корпуса.
bm, lost = main_body(base_v2)
report['корпус_мелочь_мм3'] = round(lost, 3)
cm, lost = main_body(cover_v2_seated)
report['крышка_мелочь_мм3'] = round(lost, 3)
lid_outline = section_poly(to_tm(cm), 10.8)[0]
bm_tex = texture.apply(bm, body, -PLATE_T + 0.9, 8.6, seed=1, mode=TEXTURE)
cm_tex = texture.apply(cm, lid_outline, 9.15, 11.0, seed=2, mode=TEXTURE)
report.update(фактура_треугольников_корпус=bm_tex.num_tri(), фактура_треугольников_крышка=cm_tex.num_tri(),
              фактура_пересечение_корпус_крышка=round((bm_tex ^ cm_tex).volume(), 2),
              фактура_клипса_в_корпус_мм3=round((clip ^ bm_tex).volume(), 3),
              фактура_тел=[len(bm_tex.decompose()), len(cm_tex.decompose())])
bm, cm = bm_tex, cm_tex

# ---------------- экспорт: всё на одном столе, плоскостью на стол (Z вверх) ----------------
# +90° вокруг X: z' = y. У корпуса минимальный Y — спинка с фаской, у крышки (в своей системе,
# до поворота на 180° вокруг Z) — лицо с эмблемой: обе большие плоскости ложатся на стол
Rb = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])


def to_bed(mesh, transform=Rb, lift=None):
    m = mesh.copy()
    m.apply_transform(transform)
    shift = lift if lift is not None else -m.bounds[0]
    m.apply_translation(shift)
    return m, shift


bt = to_tm(bm)
bt.export(f"{OUT}\\v2_base_model.stl")
bt_print, _ = to_bed(bt)

# клипса: лицевой стороной язычка на стол (та же плоскость, что спинка корпуса) — слои вдоль язычка
clm, lost = main_body(clip)
report['клипса_мелочь_мм3'] = round(lost, 3)
clt = to_tm(clm)
clt.export(f"{OUT}\\v2_clip_seated.stl")
cl_print, _ = to_bed(clt)

ct = to_tm(cm)
ct.export(f"{OUT}\\v2_cover_seated.stl")
to_cover_print = Rb @ np.linalg.inv(T)
ct_print, lift = to_bed(ct, to_cover_print)
cover_parts = [(ct_print, "крышка")]
if emblem_seated is not None:
    et = to_tm(emblem_seated)
    et.export(f"{OUT}\\v2_emblem_seated.stl")
    et_print, _ = to_bed(et, to_cover_print, lift)   # тот же сдвиг, что у крышки: вкладыш стоит в своём гнезде
    cover_parts.append((et_print, "эмблема"))
    report["эмблема_объём_мм3"] = round(et_print.volume, 2)
    report["эмблема_в_крышку_мм3"] = round((emblem_seated ^ cm).volume(), 3)

# раскладка в ряд с зазором GAP_BED, ряд по центру стола K2 (260×260)
GAP_BED, BED = 8.0, 260.0
row = [("корпус", [(bt_print, "корпус")]), ("крышка с эмблемой", cover_parts), ("клипса", [(cl_print, "клипса")])]
widths_bed = [max(m.bounds[1][0] for m, _ in parts) for _, parts in row]
depth_bed = max(max(m.bounds[1][1] for m, _ in parts) for _, parts in row)
x = (BED - (sum(widths_bed) + GAP_BED * (len(row) - 1))) / 2
plate_objs = []
for (name, parts), w in zip(row, widths_bed):
    plate_objs.append((name, parts, (x, (BED - depth_bed) / 2)))
    x += w + GAP_BED
export_plate(plate_objs, print_file("plate", "3mf"))
report["стол_занято_мм"] = [round(x - GAP_BED - (BED - (sum(widths_bed) + GAP_BED * (len(row) - 1))) / 2, 1),
                            round(depth_bed, 1)]
report["корпус_печать_габарит"] = [round(v, 2) for v in bt_print.extents]
report["крышка_печать_габарит"] = [round(v, 2) for v in ct_print.extents]
report["клипса_печать_габарит"] = [round(v, 2) for v in cl_print.extents]
print(json.dumps(report, ensure_ascii=False, indent=1))
