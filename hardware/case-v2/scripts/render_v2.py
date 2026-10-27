"""Blender: рендеры корпуса v2. blender -b -P render_v2.py -- <папка out> <папка renders>"""
import math
import sys

import bpy
from mathutils import Vector

out, rdir = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
engines = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else engines[0]
scene.render.resolution_x, scene.render.resolution_y = 1100, 850
scene.view_settings.view_transform = "Standard"

world = bpy.data.worlds.new("w")
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.93, 0.94, 0.96, 1)
bg.inputs[1].default_value = 1.0
scene.world = world


def mat(name, rgba, rough=0.55):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = rgba
    p.inputs["Roughness"].default_value = rough
    return m


def load(path, material, name):
    bpy.ops.wm.stl_import(filepath=path)
    o = bpy.context.selected_objects[0]
    o.name = name
    o.data.materials.append(material)
    # STL в системе корпуса: Y — толщина. Переводим в Blender (Z вверх): (x, y, z) -> (x, -z, y)
    o.rotation_euler = (math.radians(90), 0, 0)
    bpy.ops.object.shade_auto_smooth() if hasattr(bpy.ops.object, "shade_auto_smooth") else None
    return o


base = load(f"{out}\\v2_base_model.stl", mat("base", (0.07, 0.07, 0.08, 1), 0.6), "base")
cover = load(f"{out}\\v2_cover_seated.stl", mat("cover", (0.16, 0.42, 0.40, 1), 0.5), "cover")
emblem = load(f"{out}\\v2_emblem_seated.stl", mat("emblem", (0.85, 0.66, 0.22, 1), 0.35), "emblem")  # вкладыш другого цвета
clip = load(f"{out}\\v2_clip_seated.stl", mat("clip", (0.55, 0.12, 0.10, 1), 0.5), "clip")  # съёмная клипса

lights = {}
for name, rot, energy in (("key", (50, 10, 35), 3.5), ("fill", (-50, -20, 210), 1.6), ("rim", (80, 0, 160), 1.2),
                          ("graze", (180 - 12, 0, 60), 0.0)):
    l = bpy.data.lights.new(name, "SUN"); l.energy = energy
    o = bpy.data.objects.new(name, l); scene.collection.objects.link(o)
    o.rotation_euler = tuple(math.radians(a) for a in rot)
    lights[name] = l


def back_light(on):
    """Сзади спинка плоская: скользящий свет (12° к плоскости, поперёк язычка) даёт тени у прорези клипсы."""
    lights["graze"].energy = 4.0 if on else 0.0
    lights["key"].energy = 0.6 if on else 3.5
    lights["fill"].energy = 0.4 if on else 1.6

cam = bpy.data.cameras.new("cam"); cam.type = "ORTHO"
co = bpy.data.objects.new("cam", cam); scene.collection.objects.link(co); scene.camera = co


def shoot(name, direction, scale=62, target=Vector((21.8, -21.3, 4.5)), up=Vector((0, 0, 1))):
    d = Vector(direction).normalized()
    co.location = target + d * 150
    co.rotation_euler = (target - co.location).to_track_quat("-Z", "Y").to_euler()
    cam.ortho_scale = scale
    scene.render.filepath = f"{rdir}\\v2_{name}.png"
    bpy.ops.render.render(write_still=True)


# в Blender: лицо крышки смотрит в +Z, спинка с клипсой — в -Z
shoot("front", (0, 0, 1))
shoot("iso_front", (0.6, -0.9, 0.9))
back_light(True)
shoot("back", (0, 0, -1))
shoot("iso_back", (-0.7, 0.8, -0.9))
back_light(False)
# сбоку вдоль оси клипсы (видно язычок и зазор): направление «вбок» по ношению
shoot("side", (math.cos(math.radians(60)), -math.sin(math.radians(60)), 0.05), scale=55)
cover.location.z += 14
emblem.location.z += 14
shoot("exploded", (0.7, -1.0, 0.8), scale=75, target=Vector((21.8, -21.3, 10)))

# клипса выдвинута из кармана вниз по ношению (вдоль −u) и отнесена от спинки
cover.location.z -= 14; emblem.location.z -= 14
u = Vector((math.cos(math.radians(330)), -math.sin(math.radians(330)), 0))  # (x, z) STL -> (x, -y) Blender
clip.location = -u * 22 + Vector((0, 0, -6))
back_light(True)
shoot("clip_out", (-0.7, 0.8, -0.9), scale=90, target=Vector((21.8, -21.3, -1)))
