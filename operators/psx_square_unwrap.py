import bmesh
from bpy.types import Operator

from ..utils.psx_uv_utils import (
    ensure_uv_layer,
    find_connected_quad_chains,
    fit_each_face_to_bounds,
    get_view3d_region_data,
    gridify_quad,
    has_selected_faces,
    is_horizontal_face,
    order_quad_by_cardinal,
    order_quad_by_view,
    order_quad_for_chain,
    separate_faces_on_grid,
    stretch_face_from_view,
)


def _unwrap_selected_faces(obj, mode, region=None, region_3d=None):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()

    selected_faces = [face for face in bm.faces if face.select]
    if not selected_faces:
        return 0, 0, []

    uv = ensure_uv_layer(bm)
    processed = []
    skipped_reasons = []

    if mode == 'CHAIN':
        processed, skipped_reasons = _unwrap_chain_faces(
            obj, selected_faces, uv, region, region_3d
        )
    else:
        for face in selected_faces:
            result = _unwrap_single_face(face, obj, mode, uv, region, region_3d)
            if result is True:
                processed.append(face)
            else:
                skipped_reasons.append(result)

    if processed:
        separate_faces_on_grid(processed, uv)
        fit_each_face_to_bounds(processed, uv)

    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return len(processed), len(skipped_reasons), skipped_reasons


def _unwrap_single_face(face, obj, mode, uv, region, region_3d):
    if mode == 'CARDINAL':
        if len(face.loops) != 4:
            return "non-quad"
        if not is_horizontal_face(face, obj):
            return "not horizontal"
        ordered = order_quad_by_cardinal(face, obj)
        if ordered is None or len(set(ordered)) != 4:
            return "could not orient"
        gridify_quad(ordered, uv)
        return True

    if len(face.loops) == 4:
        ordered = order_quad_by_view(face, obj, region, region_3d)
        if ordered is None or len(set(ordered)) != 4:
            stretch_face_from_view(face, obj, uv, region, region_3d)
            return True
        gridify_quad(ordered, uv)
        return True

    stretch_face_from_view(face, obj, uv, region, region_3d)
    return True


def _unwrap_chain_faces(obj, selected_faces, uv, region, region_3d):
    processed = []
    skipped_reasons = []
    chains = find_connected_quad_chains(selected_faces)

    if not chains:
        for face in selected_faces:
            skipped_reasons.append("non-quad" if len(face.loops) != 4 else "broken chain")
        return processed, skipped_reasons

    handled = set()
    for chain in chains:
        if not chain:
            continue

        for strip_index, face in enumerate(chain):
            prev_face = chain[strip_index - 1] if strip_index > 0 else None
            next_face = chain[strip_index + 1] if strip_index + 1 < len(chain) else None

            if len(chain) == 1:
                ordered = order_quad_by_view(face, obj, region, region_3d)
            else:
                ordered = order_quad_for_chain(face, prev_face, next_face, obj)

            if ordered is None or len(set(ordered)) != 4:
                skipped_reasons.append("broken chain")
                handled.add(face)
                continue

            gridify_quad(ordered, uv)
            processed.append(face)
            handled.add(face)

    for face in selected_faces:
        if face not in handled:
            skipped_reasons.append("non-quad" if len(face.loops) != 4 else "broken chain")

    return processed, skipped_reasons


def _run_unwrap(context, operator, mode):
    objects = [obj for obj in context.objects_in_mode_unique_data if obj.type == 'MESH']
    if not objects:
        operator.report({'ERROR'}, "Active object must be a mesh")
        return {'CANCELLED'}

    region = region_3d = None
    if mode in {'VIEWPORT', 'CHAIN'}:
        region, region_3d = get_view3d_region_data(context)
        if mode == 'VIEWPORT' and (region is None or region_3d is None):
            operator.report({'ERROR'}, "Run this operator from a 3D Viewport")
            return {'CANCELLED'}

    processed_count = 0
    skipped_count = 0
    skipped_reasons = []

    for obj in objects:
        count, skipped, reasons = _unwrap_selected_faces(obj, mode, region, region_3d)
        processed_count += count
        skipped_count += skipped
        skipped_reasons.extend(reasons)

    if processed_count == 0 and skipped_count == 0:
        operator.report({'WARNING'}, "No faces selected")
        return {'CANCELLED'}

    if processed_count == 0:
        operator.report({'WARNING'}, _skip_message(mode, skipped_reasons))
        return {'CANCELLED'}

    message = _success_message(mode, processed_count, skipped_count, skipped_reasons)
    operator.report({'INFO'}, message)
    return {'FINISHED'}


def _skip_message(mode, skipped_reasons):
    if mode == 'CARDINAL':
        if skipped_reasons.count("non-quad") == len(skipped_reasons):
            return "No selected quad faces found (cardinal mode requires quads)"
        if skipped_reasons.count("not horizontal") == len(skipped_reasons):
            return "No selected horizontal quad faces found (normal must align with world Z)"
        return "No valid horizontal quads found to unwrap"
    if mode == 'CHAIN':
        if skipped_reasons.count("non-quad") == len(skipped_reasons):
            return "No selected quad faces found (chain mode requires quads)"
        return "No connected quad chains found (faces must share edges)"
    return "No selected faces found to unwrap"


def _success_message(mode, processed_count, skipped_count, skipped_reasons):
    labels = {
        'VIEWPORT': "Viewport unwrap",
        'CARDINAL': "Cardinal unwrap",
        'CHAIN': "Chain unwrap",
    }
    message = f"{labels[mode]}: {processed_count} face(s)"
    if skipped_count:
        unique = sorted(set(skipped_reasons))
        reason_text = ", ".join(unique)
        message += f"; skipped {skipped_count} face(s) ({reason_text})"
    return message


class PsxUnwrapViewport(Operator):
    bl_idname = "uv.toolkit_psx_unwrap_viewport"
    bl_label = "Unwrap (Viewport)"
    bl_description = (
        "Orient selected faces from the current view, Gridify quads into squares, "
        "separate each face, then Fit to Bounds (0-1)"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and has_selected_faces(context)

    def execute(self, context):
        return _run_unwrap(context, self, 'VIEWPORT')


class PsxUnwrapCardinal(Operator):
    bl_idname = "uv.toolkit_psx_unwrap_cardinal"
    bl_label = "Unwrap (Cardinal / Horizontal)"
    bl_description = (
        "Orient horizontal quads by world N/S/E/W, Gridify into squares, "
        "separate each face, then Fit to Bounds (0-1)"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and has_selected_faces(context)

    def execute(self, context):
        return _run_unwrap(context, self, 'CARDINAL')


class PsxUnwrapChain(Operator):
    bl_idname = "uv.toolkit_psx_unwrap_chain"
    bl_label = "Unwrap (Chain / End-to-End)"
    bl_description = (
        "Orient connected quad strips face-to-face, Gridify into squares, "
        "separate each face, then Fit to Bounds (0-1)"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and has_selected_faces(context)

    def execute(self, context):
        return _run_unwrap(context, self, 'CHAIN')
