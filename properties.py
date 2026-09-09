from bpy.types import PropertyGroup
from bpy.props import IntProperty, FloatProperty, EnumProperty, StringProperty


class UvToolkitPsxProperties(PropertyGroup):
    texture_size: IntProperty(
        name="Texture Size",
        description="Reference texture resolution in pixels (e.g. 1024)",
        default=1024,
        min=1,
        soft_max=8192,
    )
    grid_divisions: IntProperty(
        name="Divisions",
        description="Number of sections the UV map is split into along each axis",
        default=8,
        min=1,
        max=64,
    )
    section_scale_multiplier: FloatProperty(
        name="Section Scale",
        description=(
            "Multiplier for section UV size. 1.0 fits a full 0-1 island into one "
            "section; 2.0 makes it twice that size"
        ),
        default=1.0,
        min=0.01,
        soft_max=8.0,
        precision=3,
    )


class UvToolkitProperties(PropertyGroup):
    checker_map_width: IntProperty(
        name="Width",
        min=0
    )
    checker_map_height: IntProperty(
        name="Height",
        min=0
    )
    move_distance: FloatProperty(
        name="Distance",
        default=1.0,
        min=0,
        step=0.1,
    )

    island_rotation_angle: IntProperty(
        name="Angle",
        default=90,
        min=0,
    )

    island_rotation_mode: EnumProperty(
        name="Rotation Mode",
        description="Current rotation mode",
        items=[
            ("GLOBAL", "G", "Global rotation"),
            ("LOCAL", "L", "Local rotation"),
        ],
        default="LOCAL",
    )

    align_mode: EnumProperty(
        name="Mode",
        items=[
            ("VERTICES", "Vertices", ""),
            ("ISLANDS", "Islands", "")
        ],
    )

    island_scale_x: FloatProperty(
        name="X",
        default=1,
    )

    island_scale_y: FloatProperty(
        name="Y",
        default=1,
    )

    island_scale_mode: EnumProperty(
        name="Scale Mode",
        description="Current scale mode",
        items=[
            ("GLOBAL", "G", "Global scale"),
            ("LOCAL", "L", "Local scale"),
        ],
        default="GLOBAL",
    )

    uv_layer_name: StringProperty(
        name="",
    )
    uv_layer_index: IntProperty(
        name="",
        description="Channel",
        min=1,
        default=1,
    )
