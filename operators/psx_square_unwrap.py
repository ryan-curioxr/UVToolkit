import bmesh
from bpy.types import Operator

from ..utils.psx_uv_utils import (
    collect_unwrap_pieces,
    ensure_uv_layer,
    find_connected_quad_chains,
    fit_each_island_to_bounds,
    get_view3d_region_data,
    gridify_piece,
    has_selected_faces,
    is_horizontal_face,
    is_quad_like_piece,
    order_piece_for_chain,
    order_verts_by_cardinal,
    order_verts_by_view,
    scale_selected_islands_to_section,
    separate_islands_on_grid,
    snap_selected_islands_to_section_centers,
    stretch_face_from_view,
    unique_verts_from_faces,
)


def _unwrap_selected_faces(obj, mode, region=None, region_3d=None):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()

    selected_faces = [face for face in bm.faces if face.select]
    if not selected_faces:
        return 0, 0, []

    uv = ensure_uv_layer(bm)
    processed_islands = []
    skipped_reasons = []

    if mode == 'CHAIN':
        processed_islands, skipped_reasons = _unwrap_chain_pieces(
            obj, selected_faces, uv, region, region_3d
        )
    else:
        for piece in collect_unwrap_pieces(selected_faces):
            result = _unwrap_piece(piece, obj, mode, uv, region, region_3d)
            if result is True:
                processed_islands.append(list(piece))
            else:
                skipped_reasons.extend([result] * len(piece))

    if processed_islands:
        separate_islands_on_grid(processed_islands, uv)
        fit_each_island_to_bounds(processed_islands, uv)

    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    processed_count = sum(len(island) for island in processed_islands)
    return processed_count, len(skipped_reasons), skipped_reasons


def _unwrap_piece(piece, obj, mode, uv, region, region_3d):
    if is_quad_like_piece(piece):
        if mode == 'CARDINAL':
            if not all(is_horizontal_face(face, obj) for face in piece):
                return "not horizontal"
            ordered_verts = order_verts_by_cardinal(unique_verts_from_faces(piece), obj)
            if ordered_verts is None or len(set(ordered_verts)) != 4:
                return "could not orient"
            gridify_piece(piece, ordered_verts, uv)
            return True

        ordered_verts = order_verts_by_view(
            unique_verts_from_faces(piece), obj, region, region_3d
        )
        if ordered_verts is None or len(set(ordered_verts)) != 4:
            if len(piece) == 1:
                stretch_face_from_view(piece[0], obj, uv, region, region_3d)
                return True
            return "could not orient"
        gridify_piece(piece, ordered_verts, uv)
        return True

    if mode == 'CARDINAL':
        return "non-quad"
    if len(piece) == 1:
        stretch_face_from_view(piece[0], obj, uv, region, region_3d)
        return True
    return "could not orient"


def _unwrap_chain_pieces(obj, selected_faces, uv, region, region_3d):
    processed_islands = []
    skipped_reasons = []
    chains = find_connected_quad_chains(selected_faces)

    if not chains:
        for face in selected_faces:
            skipped_reasons.append("non-quad" if len(face.loops) != 4 else "broken chain")
        return processed_islands, skipped_reasons

    handled = set()
    for chain in chains:
        if not chain:
            continue

        for strip_index, piece in enumerate(chain):
            prev_piece = chain[strip_index - 1] if strip_index > 0 else None
            next_piece = chain[strip_index + 1] if strip_index + 1 < len(chain) else None

            if len(chain) == 1:
                ordered_verts = order_verts_by_view(
                    unique_verts_from_faces(piece), obj, region, region_3d
                )
            else:
                ordered_verts = order_piece_for_chain(piece, prev_piece, next_piece, obj)

            if ordered_verts is None or len(set(ordered_verts)) != 4:
                skipped_reasons.extend(["broken chain"] * len(piece))
                handled.update(piece)
                continue

            gridify_piece(piece, ordered_verts, uv)
            processed_islands.append(list(piece))
            handled.update(piece)

    for face in selected_faces:
        if face not in handled:
            skipped_reasons.append("non-quad" if len(face.loops) != 4 else "broken chain")

    return processed_islands, skipped_reasons


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

    scaled_count, target_span, pixel_size = scale_selected_islands_to_section(context)
    snap_selected_islands_to_section_centers(context)
    message = _success_message(mode, processed_count, skipped_count, skipped_reasons)
    if scaled_count:
        message += f"; scaled to {target_span:.4f} UV ({pixel_size}px)"
    operator.report({'INFO'}, message)
    return {'FINISHED'}


def _skip_message(mode, skipped_reasons):
    if mode == 'CARDINAL':
        if skipped_reasons.count("non-quad") == len(skipped_reasons):
            return "No selected quads or triangle-pairs found (cardinal mode requires quads)"
        if skipped_reasons.count("not horizontal") == len(skipped_reasons):
            return "No selected horizontal quad faces found (normal must align with world Z)"
        return "No valid horizontal quads found to unwrap"
    if mode == 'CHAIN':
        if skipped_reasons.count("non-quad") == len(skipped_reasons):
            return "No selected quads or triangle-pairs found (chain mode requires quads)"
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
        "Orient selected faces from the current view, Gridify quads and triangle-pairs "
        "into squares, separate islands, Fit to Bounds (0-1), then scale to section size"
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
        "Orient horizontal quads and triangle-pairs by world N/S/E/W, Gridify into squares, "
        "separate islands, Fit to Bounds (0-1), then scale to section size"
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
        "Orient connected quad or triangle-pair strips face-to-face, Gridify into squares, "
        "separate islands, Fit to Bounds (0-1), then scale to section size"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and has_selected_faces(context)

    def execute(self, context):
        return _run_unwrap(context, self, 'CHAIN')
