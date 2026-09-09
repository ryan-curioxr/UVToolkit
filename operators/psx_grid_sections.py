import bmesh
from bpy.types import Operator
from bpy.props import EnumProperty, FloatProperty

from ..utils.psx_uv_utils import (
    DIRECTION_OFFSETS,
    calc_bbox_center,
    ensure_uv_layer,
    get_bbox,
    get_selected_uv_islands,
    has_selected_faces,
    scale_island_to_span,
    section_center_uv,
    section_uv_size,
    translate_island,
    uv_to_section,
)


class PsxMoveSection(Operator):
    bl_idname = "uv.toolkit_psx_move_section"
    bl_label = "Move Island to Section"
    bl_description = (
        "Move selected UV islands so each island center snaps to the center "
        "of the adjacent grid section"
    )
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(
        name="Direction",
        items=(
            ('UP', "Up", "Move to the section above"),
            ('DOWN', "Down", "Move to the section below"),
            ('LEFT', "Left", "Move to the section on the left"),
            ('RIGHT', "Right", "Move to the section on the right"),
        ),
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and has_selected_faces(context)

    def execute(self, context):
        settings = context.scene.uv_toolkit_psx
        divisions = settings.grid_divisions
        delta_col, delta_row = DIRECTION_OFFSETS[self.direction]

        moved_count = 0
        blocked_count = 0

        for obj in context.objects_in_mode_unique_data:
            if obj.type != 'MESH':
                continue

            bm = bmesh.from_edit_mesh(obj.data)
            uv = ensure_uv_layer(bm)
            islands = get_selected_uv_islands(bm, uv)
            if not islands:
                continue

            for island_faces in islands:
                bbox = get_bbox(uv, island_faces)
                center_u, center_v = calc_bbox_center(bbox)
                col, row = uv_to_section(center_u, center_v, divisions)

                target_col = col + delta_col
                target_row = row + delta_row
                if not (0 <= target_col < divisions and 0 <= target_row < divisions):
                    blocked_count += 1
                    continue

                target_u, target_v = section_center_uv(target_col, target_row, divisions)
                translate_island(uv, island_faces, target_u - center_u, target_v - center_v)
                moved_count += 1

            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

        if moved_count == 0 and blocked_count == 0:
            self.report({'WARNING'}, "No selected UV islands found")
            return {'CANCELLED'}

        if moved_count == 0:
            self.report({'WARNING'}, "Selected islands are already at the grid edge")
            return {'CANCELLED'}

        if blocked_count:
            self.report(
                {'INFO'},
                f"Moved {moved_count} island(s); {blocked_count} blocked at grid edge",
            )
        else:
            self.report({'INFO'}, f"Moved {moved_count} island(s)")
        return {'FINISHED'}


class PsxSetSectionScale(Operator):
    bl_idname = "uv.toolkit_psx_set_section_scale"
    bl_label = "Set Section Scale"
    bl_description = "Set the section scale multiplier preset"
    bl_options = {'REGISTER', 'UNDO'}

    multiplier: FloatProperty(name="Multiplier", default=1.0, min=0.01)

    def execute(self, context):
        context.scene.uv_toolkit_psx.section_scale_multiplier = self.multiplier
        return {'FINISHED'}


class PsxScaleToSection(Operator):
    bl_idname = "uv.toolkit_psx_scale_to_section"
    bl_label = "Scale Islands to Section Size"
    bl_description = (
        "Scale selected UV islands around each island center so the largest "
        "side matches the current grid section size"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and has_selected_faces(context)

    def execute(self, context):
        settings = context.scene.uv_toolkit_psx
        target_span = section_uv_size(
            settings.grid_divisions,
            settings.section_scale_multiplier,
        )

        scaled_count = 0
        for obj in context.objects_in_mode_unique_data:
            if obj.type != 'MESH':
                continue

            bm = bmesh.from_edit_mesh(obj.data)
            uv = ensure_uv_layer(bm)
            islands = get_selected_uv_islands(bm, uv)
            if not islands:
                continue

            for island_faces in islands:
                scale_island_to_span(uv, island_faces, target_span)
                scaled_count += 1

            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

        if scaled_count == 0:
            self.report({'WARNING'}, "No selected UV islands found")
            return {'CANCELLED'}

        pixel_size = int(round(settings.texture_size * target_span))
        self.report(
            {'INFO'},
            f"Scaled {scaled_count} island(s) to {target_span:.4f} UV ({pixel_size}px)",
        )
        return {'FINISHED'}
