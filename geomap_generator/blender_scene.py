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


def clear_geomap_child_collection(child_name: str) -> int:
    assert_main_thread()
    root = bpy.data.collections.get("GeoMap")
    if root is None:
        return 0
    child = root.children.get(child_name)
    if child is None:
        return 0

    count = 0
    for obj in list(child.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
        count += 1
    return count


def material_named(mat_name: str, color: tuple[float, float, float, float]):
    assert_main_thread()
    material = bpy.data.materials.get(mat_name) or bpy.data.materials.new(mat_name)
    material.diffuse_color = color
    set_material_roughness(material, 1.0)
    return material


def set_material_roughness(material, value: float = 1.0) -> None:
    material.roughness = value
    if not material.use_nodes or material.node_tree is None:
        return
    for node in material.node_tree.nodes:
        if getattr(node, "type", None) != "BSDF_PRINCIPLED":
            continue
        roughness_input = node.inputs.get("Roughness")
        if roughness_input is not None:
            roughness_input.default_value = value


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
