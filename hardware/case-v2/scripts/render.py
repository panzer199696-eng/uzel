"""Blender: рендер STL с нескольких сторон. blender -b -P render.py -- out_prefix file1.stl [file2.stl ...]"""
import math
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
out_prefix, files = argv[0], argv[1:]

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items] else "BLENDER_EEVEE"
scene.render.resolution_x = 900
scene.render.resolution_y = 700
scene.render.film_transparent = False
world = bpy.data.worlds.new("w")
world.color = (0.92, 0.92, 0.95)
scene.world = world

colors = [(0.85, 0.45, 0.15, 1), (0.2, 0.5, 0.85, 1), (0.3, 0.7, 0.3, 1), (0.6, 0.6, 0.6, 1)]
objs = []
for i, f in enumerate(files):
    bpy.ops.wm.stl_import(filepath=f)
    o = bpy.context.selected_objects[0]
    mat = bpy.data.materials.new(f"m{i}")
    mat.diffuse_color = colors[i % len(colors)]
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = colors[i % len(colors)]
    o.data.materials.append(mat)
    objs.append(o)

# центр и размер сцены
pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
center = sum(pts, Vector()) / len(pts)
size = max((p - center).length for p in pts)

sun = bpy.data.lights.new("sun", "SUN"); sun.energy = 3
so = bpy.data.objects.new("sun", sun); scene.collection.objects.link(so)
so.rotation_euler = (math.radians(40), math.radians(20), math.radians(30))
fill = bpy.data.lights.new("fill", "SUN"); fill.energy = 1.5
fo = bpy.data.objects.new("fill", fill); scene.collection.objects.link(fo)
fo.rotation_euler = (math.radians(-60), math.radians(-30), math.radians(200))

cam = bpy.data.cameras.new("cam"); cam.type = "ORTHO"; cam.ortho_scale = size * 2.3
co = bpy.data.objects.new("cam", cam); scene.collection.objects.link(co); scene.camera = co

views = {
    "iso_top": Vector((1, -1.2, 1.4)),
    "iso_bottom": Vector((-1, 1.2, -1.4)),
    "front": Vector((0, -1, 0.15)),
    "inside": Vector((0.2, 1.5, 0.3)),
}
for name, d in views.items():
    d = d.normalized()
    co.location = center + d * size * 4
    co.rotation_euler = (center - co.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = f"{out_prefix}_{name}.png"
    bpy.ops.render.render(write_still=True)
