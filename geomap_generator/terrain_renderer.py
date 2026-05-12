import bpy

from .blender_scene import link_to_geomap_collection, set_active
from .mesh_builder import BboxPlaneBuilder, DemMeshBuilder
from .models import BoundingBox, DemGrid, SatelliteTile
from .scene_units import SceneScale, scaled_map_value
from .threading_utils import assert_main_thread


class TerrainRenderer:
    def create_satellite_bbox(
        self,
        context,
        tile: SatelliteTile,
        projection_bbox: BoundingBox,
        settings,
        index: int,
    ) -> None:
        assert_main_thread()
        verts, edges, faces = BboxPlaneBuilder().build(
            tile.bbox, settings.detail_level, projection_bbox=projection_bbox
        )
        mesh = bpy.data.meshes.new(f"GeoMap_Satellite_Tile_{index:03d}_Mesh")
        obj = bpy.data.objects.new(f"GeoMap_Satellite_Tile_{index:03d}", mesh)
        link_to_geomap_collection(context, obj, "Textures")
        set_active(context, obj)
        mesh.from_pydata(verts, edges, faces)
        mesh.update()

        uv_layer = mesh.uv_layers.new(name="SatelliteUV")
        uv_coords = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        for poly in mesh.polygons:
            for loop_index in poly.loop_indices:
                uv_layer.data[loop_index].uv = uv_coords[mesh.loops[loop_index].vertex_index]

        material = bpy.data.materials.new("GeoMap_Satellite_Material")
        if tile.image_path and not self._apply_image_texture(material, tile.image_path):
            material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
        obj.data.materials.append(material)
        obj["geomap_layer"] = "satellite_bbox"

    def create_dem_mesh(
        self,
        context,
        dem_grid: DemGrid,
        settings,
        height_scale: float,
        scene_scale: SceneScale | None = None,
        texture_path=None,
        projection_bbox: BoundingBox | None = None,
        suffix: str = "",
        visible: bool = True,
        min_elevation_override: float | None = None,
    ):
        assert_main_thread()
        verts, edges, faces = DemMeshBuilder().build(
            dem_grid,
            settings.detail_level,
            height_scale,
            projection_bbox=projection_bbox,
            min_elevation_override=min_elevation_override,
        )
        base_height = scaled_map_value(settings.print_base_height, scene_scale)
        if settings.output_preset == "PRINT_3D" and base_height > 0.0:
            verts, faces = self._add_print_base(
                verts, faces, dem_grid.rows, dem_grid.cols, base_height
            )
        mesh = bpy.data.meshes.new(f"GeoMap_DEM_Terrain{suffix}_Mesh")
        obj = bpy.data.objects.new(f"GeoMap_DEM_Terrain{suffix}", mesh)
        link_to_geomap_collection(context, obj, "Terrain")
        if visible:
            set_active(context, obj)
        mesh.from_pydata(verts, edges, faces)
        mesh.update()

        material = bpy.data.materials.new("GeoMap_DEM_Material")
        if texture_path:
            uv_layer = mesh.uv_layers.new(name="SatelliteUV")
            uv_coords = DemMeshBuilder.uv_coords(dem_grid.rows, dem_grid.cols)
            for poly in mesh.polygons:
                for loop_index in poly.loop_indices:
                    vertex_index = mesh.loops[loop_index].vertex_index
                    if vertex_index < len(uv_coords):
                        uv_layer.data[loop_index].uv = uv_coords[vertex_index]

            if not self._apply_image_texture(material, texture_path):
                material.diffuse_color = (0.35, 0.45, 0.25, 1.0)
        else:
            material.diffuse_color = (0.35, 0.45, 0.25, 1.0)
        obj.data.materials.append(material)
        obj["geomap_layer"] = "dem"
        obj["geomap_dem_min_m"] = round(dem_grid.min_elevation(), 2)
        obj["geomap_dem_max_m"] = round(dem_grid.max_elevation(), 2)
        obj["geomap_dem_height_scale_bu_per_m"] = round(height_scale, 8)
        obj["geomap_print_base_height"] = (
            base_height if settings.output_preset == "PRINT_3D" else 0.0
        )
        if not visible:
            obj.hide_viewport = True
            obj.hide_render = True
        return obj

    @staticmethod
    def _add_print_base(
        verts: list[tuple[float, float, float]],
        faces: list[tuple[int, int, int, int]],
        rows: int,
        cols: int,
        base_height: float,
    ) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int, int]]]:
        bottom_z = min((vertex[2] for vertex in verts), default=0.0) - base_height
        bottom_start = len(verts)
        base_verts = [(x, y, bottom_z) for x, y, _z in verts]
        base_faces = list(faces)

        for row in range(rows - 1):
            a = row * cols
            b = (row + 1) * cols
            base_faces.append((a, b, bottom_start + b, bottom_start + a))
            c = row * cols + cols - 1
            d = (row + 1) * cols + cols - 1
            base_faces.append((c, bottom_start + c, bottom_start + d, d))

        for col in range(cols - 1):
            a = col
            b = col + 1
            base_faces.append((a, bottom_start + a, bottom_start + b, b))
            c = (rows - 1) * cols + col
            d = c + 1
            base_faces.append((c, d, bottom_start + d, bottom_start + c))

        for row in range(rows - 1):
            for col in range(cols - 1):
                a = bottom_start + row * cols + col
                b = a + 1
                c = a + cols + 1
                d = a + cols
                base_faces.append((a, d, c, b))

        return [*verts, *base_verts], base_faces

    @staticmethod
    def _find_principled_bsdf(material):
        if not material.use_nodes or material.node_tree is None:
            return None
        for node in material.node_tree.nodes:
            if getattr(node, "type", None) == "BSDF_PRINCIPLED":
                return node
        return TerrainRenderer._find_named_item(material.node_tree.nodes, "Principled BSDF")

    @staticmethod
    def _find_named_item(collection, name: str):
        if collection is None:
            return None
        try:
            item = collection[name]
        except (KeyError, TypeError):
            item = None
        if item is not None:
            return item
        for item in collection:
            if getattr(item, "name", None) == name:
                return item
        return None

    @classmethod
    def _apply_image_texture(cls, material, image_path) -> bool:
        material.use_nodes = True
        if material.node_tree is None:
            return False

        bsdf = cls._find_principled_bsdf(material)
        if bsdf is None:
            return False

        image = bpy.data.images.load(str(image_path), check_existing=True)
        tex_node = material.node_tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = image

        color_output = cls._find_named_item(tex_node.outputs, "Color")
        base_color_input = cls._find_named_item(bsdf.inputs, "Base Color")
        if color_output is None or base_color_input is None:
            return False

        material.node_tree.links.new(color_output, base_color_input)
        return True
