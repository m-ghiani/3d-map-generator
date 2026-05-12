import bpy

from .threading_utils import assert_main_thread


def link_to_geomap_collection(context, obj, child_name: str) -> None:
    assert_main_thread()
    root = bpy.data.collections.get("GeoMap")
    if root is None:
        root = bpy.data.collections.new("GeoMap")
        context.scene.collection.children.link(root)

    child = root.children.get(child_name)
    if child is None:
        child = bpy.data.collections.new(child_name)
        root.children.link(child)
    child.objects.link(obj)


def material_named(mat_name: str, color: tuple[float, float, float, float]):
    assert_main_thread()
    material = bpy.data.materials.get(mat_name) or bpy.data.materials.new(mat_name)
    material.diffuse_color = color
    return material


def set_active(context, obj) -> None:
    assert_main_thread()
    for selected in context.selected_objects:
        selected.select_set(False)
    context.view_layer.objects.active = obj
    obj.select_set(True)


def create_text_object(
    context,
    text: str,
    location: tuple[float, float, float],
    object_name: str,
    size: float,
    material,
) -> None:
    assert_main_thread()
    curve = bpy.data.curves.new(f"{object_name}_Curve", "FONT")
    curve.body = text
    curve.size = size
    curve.align_x = "LEFT"
    curve.align_y = "CENTER"
    obj = bpy.data.objects.new(object_name, curve)
    obj.location = location
    link_to_geomap_collection(context, obj, "Annotations")
    obj.data.materials.append(material)


def create_quad_mesh_object(
    context,
    object_name: str,
    verts: list[tuple[float, float, float]],
    collection_name: str,
    material,
):
    assert_main_thread()
    mesh = bpy.data.meshes.new(f"{object_name}_Mesh")
    obj = bpy.data.objects.new(object_name, mesh)
    link_to_geomap_collection(context, obj, collection_name)
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update()
    obj.data.materials.append(material)
    return obj
