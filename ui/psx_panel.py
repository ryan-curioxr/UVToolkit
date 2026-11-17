from bpy.types import Panel

from ..utils.psx_uv_utils import section_pixel_size, section_uv_size


class UVTOOLKIT_PT_psx_square_unwrap(Panel):
    bl_label = "Square Unwrap"
    bl_idname = "UVTOOLKIT_PT_psx_square_unwrap"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "PSX UV Tools"

    def draw(self, context):
        layout = self.layout

        col = layout.column(align=True)
        col.operator("uv.toolkit_psx_unwrap_viewport", text="Unwrap (Viewport)", icon='UV')
        col.operator(
            "uv.toolkit_psx_unwrap_cardinal",
            text="Unwrap (Cardinal / Horizontal)",
            icon='ORIENTATION_GLOBAL',
        )
        col.operator(
            "uv.toolkit_psx_unwrap_chain",
            text="Unwrap (Chain / End-to-End)",
            icon='MOD_ARRAY',
        )

        info = layout.column(align=True)
        info.scale_y = 0.8
        info.label(text="Selected faces only", icon='INFO')
        info.label(text="Orient → Gridify → Separate → Fit to Bounds → Scale to Section")
        info.label(text="Viewport: quads, triangle-pairs, and other faces")
        info.label(text="Cardinal: horizontal quads / triangle-pairs, N/S/E/W")
        info.label(text="Chain: connected quads / triangle-pairs, then separated")


class UVTOOLKIT_PT_psx_grid_sections(Panel):
    bl_label = "Grid Sections"
    bl_idname = "UVTOOLKIT_PT_psx_grid_sections"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "PSX UV Tools"

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.ui_type == 'UV'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.uv_toolkit_psx

        layout.prop(settings, "texture_size")
        layout.prop(settings, "grid_divisions")

        pixel_size = section_pixel_size(settings.texture_size, settings.grid_divisions)
        uv_size = 1.0 / max(1, settings.grid_divisions)
        target_uv = section_uv_size(
            settings.grid_divisions,
            settings.section_scale_multiplier,
        )
        target_px = int(round(settings.texture_size * target_uv))

        layout.label(
            text=f"{settings.grid_divisions}×{settings.grid_divisions} grid, {pixel_size}px / {uv_size:.4f} UV",
            icon='INFO',
        )
        layout.label(text=f"Scale target: {target_uv:.4f} UV ({target_px}px)")

        layout.separator()
        layout.label(text="Section Scale:")

        preset_row = layout.row(align=True)
        op = preset_row.operator("uv.toolkit_psx_set_section_scale", text="0.5x")
        op.multiplier = 0.5
        op = preset_row.operator("uv.toolkit_psx_set_section_scale", text="1x")
        op.multiplier = 1.0
        op = preset_row.operator("uv.toolkit_psx_set_section_scale", text="2x")
        op.multiplier = 2.0
        op = preset_row.operator("uv.toolkit_psx_set_section_scale", text="4x")
        op.multiplier = 4.0

        layout.prop(settings, "section_scale_multiplier", text="Custom")
        layout.operator(
            "uv.toolkit_psx_scale_to_section",
            text="Scale Islands to Section Size",
            icon='FULLSCREEN_ENTER',
        )

        layout.separator()
        layout.label(text="Move UV Islands to Section:")

        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator("uv.toolkit_psx_move_section", text="", icon='TRIA_UP').direction = 'UP'

        row = col.row(align=True)
        row.operator("uv.toolkit_psx_move_section", text="", icon='TRIA_LEFT').direction = 'LEFT'
        row.operator("uv.toolkit_psx_move_section", text="", icon='TRIA_DOWN').direction = 'DOWN'
        row.operator("uv.toolkit_psx_move_section", text="", icon='TRIA_RIGHT').direction = 'RIGHT'

        info = layout.column(align=True)
        info.scale_y = 0.8
        info.label(text="Select faces in Edit Mode", icon='INFO')
        info.label(text="Moves whole islands by island center")
