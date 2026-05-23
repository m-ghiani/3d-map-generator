import bpy

from .blender_scene import link_to_geomap_collection, set_active, set_material_roughness
from .mesh_builder import BboxPlaneBuilder, BboxProjector, ContourLineBuilder, DemMeshBuilder
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
        set_material_roughness(material, 1.0)
        if tile.image_path and not self._apply_image_texture(material, tile.image_path):
            material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
            set_material_roughness(material, 1.0)
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
        set_material_roughness(material, 1.0)
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
                set_material_roughness(material, 1.0)
        else:
            material.diffuse_color = (0.35, 0.45, 0.25, 1.0)
            set_material_roughness(material, 1.0)
        obj.data.materials.append(material)
        if getattr(settings, "dem_slope_colors", False):
            self._apply_slope_colors(mesh)
        obj["geomap_layer"] = "dem"
        obj["geomap_dem_min_m"] = round(dem_grid.min_elevation(), 2)
        obj["geomap_dem_max_m"] = round(dem_grid.max_elevation(), 2)
        obj["geomap_dem_height_scale_bu_per_m"] = round(height_scale, 8)
        obj["geomap_dem_rows"] = dem_grid.rows
        obj["geomap_dem_cols"] = dem_grid.cols
        obj["geomap_print_base_height"] = (
            base_height if settings.output_preset == "PRINT_3D" else 0.0
        )
        if not visible:
            obj.hide_viewport = True
            obj.hide_render = True
        return obj

    def create_map_box(
        self,
        context,
        bbox: BoundingBox,
        settings,
        scene_scale: SceneScale,
        dem_obj=None,
    ) -> bpy.types.Object:
        assert_main_thread()
        depth = scaled_map_value(settings.map_box_depth, scene_scale)
        bot_z = -abs(depth)

        if dem_obj is not None:
            rows = dem_obj.get("geomap_dem_rows", 0)
            cols = dem_obj.get("geomap_dem_cols", 0)
            if rows >= 2 and cols >= 2:
                return self._create_map_box_from_dem(context, dem_obj, rows, cols)

        return self._create_map_box_flat(context, bbox, settings, scene_scale, bot_z)

    @staticmethod
    def _dem_perimeter_indices(rows: int, cols: int) -> list[int]:
        idx = []
        for c in range(cols):
            idx.append(c)
        for r in range(1, rows):
            idx.append(r * cols + (cols - 1))
        for c in range(cols - 2, -1, -1):
            idx.append((rows - 1) * cols + c)
        for r in range(rows - 2, 0, -1):
            idx.append(r * cols)
        return idx

    def _create_map_box_from_dem(
        self,
        context,
        dem_obj,
        rows: int,
        cols: int,
    ) -> bpy.types.Object:
        mesh_data = dem_obj.data
        matrix = dem_obj.matrix_world
        perimeter = self._dem_perimeter_indices(rows, cols)
        n = len(perimeter)

        top_verts = []
        for idx in perimeter:
            co = matrix @ mesh_data.vertices[idx].co
            top_verts.append((co.x, co.y, co.z))

        # Bottom ring flat at Z=0 (perimeter extruded down, highest lands at 0, then scaled Z=0)
        bot_verts = [(x, y, 0.0) for x, y, _z in top_verts]

        verts = top_verts + bot_verts

        faces = []
        for i in range(n):
            j = (i + 1) % n
            faces.append((i, j, n + j, n + i))
        # Bottom plate: n-gon, reversed winding so normal faces down
        faces.append(tuple(range(n * 2 - 1, n - 1, -1)))

        return self._build_map_box_object(context, verts, faces)

    def _create_map_box_flat(
        self,
        context,
        bbox: BoundingBox,
        settings,
        scene_scale: SceneScale,
        bot_z: float,
    ) -> bpy.types.Object:
        projector = BboxProjector(bbox, settings.detail_level)
        sw = projector.project(bbox.min_lat, bbox.min_lon)
        se = projector.project(bbox.min_lat, bbox.max_lon)
        ne = projector.project(bbox.max_lat, bbox.max_lon)
        nw = projector.project(bbox.max_lat, bbox.min_lon)
        top_z = 0.0
        verts = [
            (sw[0], sw[1], top_z),
            (se[0], se[1], top_z),
            (ne[0], ne[1], top_z),
            (nw[0], nw[1], top_z),
            (sw[0], sw[1], bot_z),
            (se[0], se[1], bot_z),
            (ne[0], ne[1], bot_z),
            (nw[0], nw[1], bot_z),
        ]
        faces = [
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
            (4, 5, 6, 7),
        ]
        return self._build_map_box_object(context, verts, faces)

    def create_contour_lines(
        self,
        context,
        dem_grid: DemGrid,
        settings,
        height_scale: float,
        projection_bbox: BoundingBox | None = None,
        min_elevation_override: float | None = None,
    ) -> bpy.types.Object | None:
        assert_main_thread()
        interval = getattr(settings, "contour_interval_m", 50.0)
        verts, edges, _ = ContourLineBuilder().build(
            dem_grid,
            settings.detail_level,
            height_scale,
            contour_interval_m=interval,
            projection_bbox=projection_bbox,
            min_elevation_override=min_elevation_override,
        )
        if not verts:
            return None
        mesh = bpy.data.meshes.new("GeoMap_Contours_Mesh")
        obj = bpy.data.objects.new("GeoMap_Contours", mesh)
        link_to_geomap_collection(context, obj, "Contours")
        mesh.from_pydata(verts, edges, [])
        mesh.update()
        mat = bpy.data.materials.new("GeoMap_Contours_Material")
        mat.diffuse_color = (0.58, 0.42, 0.22, 1.0)
        set_material_roughness(mat, 0.9)
        obj.data.materials.append(mat)
        obj["geomap_layer"] = "contours"
        obj["geomap_contour_interval_m"] = interval
        return obj

    def _build_map_box_object(self, context, verts, faces) -> bpy.types.Object:
        mesh = bpy.data.meshes.new("GeoMap_MapBox_Mesh")
        obj = bpy.data.objects.new("GeoMap_MapBox", mesh)
        link_to_geomap_collection(context, obj, "Terrain")
        set_active(context, obj)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        material = bpy.data.materials.new("GeoMap_MapBox_Material")
        material.diffuse_color = (0.55, 0.48, 0.38, 1.0)
        set_material_roughness(material, 0.85)
        obj.data.materials.append(material)
        obj["geomap_layer"] = "map_box"
        return obj

    @staticmethod
    def _apply_slope_colors(mesh) -> None:
        import math as _math

        mesh.calc_normals_split()
        vert_count = len(mesh.vertices)
        # Accumulate normal Z sums per vertex
        nz_sum = [0.0] * vert_count
        nz_count = [0] * vert_count
        for loop in mesh.loops:
            nz = mesh.loop_normals[loop.index].z
            nz_sum[loop.vertex_index] += nz
            nz_count[loop.vertex_index] += 1
        nz_avg = [
            (nz_sum[i] / nz_count[i]) if nz_count[i] else 1.0
            for i in range(vert_count)
        ]

        color_attr = mesh.color_attributes.new(
            name="SlopeColors", type="FLOAT_COLOR", domain="POINT"
        )
        # flat=green(0.27,0.45,0.18), steep=grey(0.55,0.50,0.45)
        for i, nz in enumerate(nz_avg):
            t = max(0.0, min(1.0, nz))
            r = 0.27 * t + 0.55 * (1.0 - t)
            g = 0.45 * t + 0.50 * (1.0 - t)
            b = 0.18 * t + 0.45 * (1.0 - t)
            color_attr.data[i].color = (r, g, b, 1.0)

        # Wire the vertex color into the material
        mat = mesh.materials[0] if mesh.materials else None
        if mat is None:
            return
        mat.use_nodes = True
        tree = mat.node_tree
        if tree is None:
            return
        # Remove existing Principled BSDF base-color link if any
        bsdf = None
        for node in tree.nodes:
            if getattr(node, "type", None) == "BSDF_PRINCIPLED":
                bsdf = node
                break
        if bsdf is None:
            return
        vc_node = tree.nodes.new("ShaderNodeVertexColor")
        vc_node.layer_name = "SlopeColors"
        base_input = bsdf.inputs.get("Base Color")
        if base_input:
            # Remove existing texture link
            for link in list(tree.links):
                if link.to_socket == base_input:
                    tree.links.remove(link)
            tree.links.new(vc_node.outputs["Color"], base_input)

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
        try:
            image.reload()
        except Exception:
            pass
        tex_node = material.node_tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = image

        color_output = cls._find_named_item(tex_node.outputs, "Color")
        base_color_input = cls._find_named_item(bsdf.inputs, "Base Color")
        if color_output is None or base_color_input is None:
            return False

        material.node_tree.links.new(color_output, base_color_input)
        set_material_roughness(material, 1.0)
        return True
