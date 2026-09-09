import bmesh
import numpy as np
from mathutils import Vector
from bpy_extras import view3d_utils

from .uv_utils import (
    calc_bbox_center,
    get_bbox,
    get_bbox_size,
    scale_matrix,
    translate_matrix,
)


HORIZONTAL_NORMAL_THRESHOLD = 0.85
UV_TOLERANCE = 1e-5

DIRECTION_OFFSETS = {
    'UP': (0, 1),
    'DOWN': (0, -1),
    'LEFT': (-1, 0),
    'RIGHT': (1, 0),
}


def vec2(value):
    return Vector((float(value[0]), float(value[1])))


def set_loop_uv(loop, uv, u, v):
    """Tuple assignment avoids Blender 5.x UV vector dimension bugs."""
    loop[uv].uv = (float(u), float(v))


def get_view3d_region_data(context):
    if context.region is not None and getattr(context, "region_data", None) is not None:
        if context.area is not None and context.area.type == 'VIEW_3D':
            return context.region, context.region_data

    screen = getattr(context, "screen", None)
    if screen is None:
        return None, None

    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        region = next((item for item in area.regions if item.type == 'WINDOW'), None)
        space = area.spaces.active
        if region is not None and space is not None and getattr(space, "region_3d", None) is not None:
            return region, space.region_3d
    return None, None


def project_to_view_2d(world_co, region, region_3d):
    """
    Project a world-space point to 2D view coordinates.
    Falls back to view-matrix X/Y when the point is off-screen or behind the camera.
    """
    if region is not None and region_3d is not None:
        point_2d = view3d_utils.location_3d_to_region_2d(region, region_3d, world_co)
        if point_2d is not None:
            return vec2(point_2d)

        view_co = region_3d.view_matrix @ world_co.to_4d()
        return vec2((view_co.x, view_co.y))
    return vec2((world_co.x, world_co.y))


def is_horizontal_face(face, obj, threshold=HORIZONTAL_NORMAL_THRESHOLD):
    world_normal = (obj.matrix_world.to_3x3() @ face.normal).normalized()
    return abs(world_normal.z) >= threshold


def face_centroid_world(face, obj):
    matrix_world = obj.matrix_world
    center = Vector((0.0, 0.0, 0.0))
    for vert in face.verts:
        center += matrix_world @ vert.co
    return center / len(face.verts)


def has_selected_faces(context):
    objects = getattr(context, "objects_in_mode_unique_data", None)
    if not objects:
        return False

    for obj in objects:
        if obj.type != 'MESH':
            continue
        bm = bmesh.from_edit_mesh(obj.data)
        if any(face.select for face in bm.faces):
            return True
    return False


def ensure_uv_layer(bm):
    return bm.loops.layers.uv.verify()


def order_quad_corners(projections):
    """Order corners as bottom-left, bottom-right, top-right, top-left."""
    min_along = min(item[1] for item in projections)
    max_along = max(item[1] for item in projections)
    along_eps = max((max_along - min_along) * 0.25, 1e-6)

    start_pair = [item for item in projections if item[1] <= min_along + along_eps]
    end_pair = [item for item in projections if item[1] >= max_along - along_eps]

    if len(start_pair) < 2 or len(end_pair) < 2:
        projections = sorted(projections, key=lambda item: (item[1], item[2]))
        start_pair = projections[:2]
        end_pair = projections[2:]

    start_pair.sort(key=lambda item: item[2])
    end_pair.sort(key=lambda item: item[2])

    return [
        start_pair[0][0],
        end_pair[0][0],
        end_pair[-1][0],
        start_pair[-1][0],
    ]


def order_quad_by_view(face, obj, region, region_3d):
    matrix_world = obj.matrix_world
    projected = []
    for loop in face.loops:
        world_co = matrix_world @ loop.vert.co
        point_2d = project_to_view_2d(world_co, region, region_3d)
        projected.append((loop, float(point_2d.x), float(point_2d.y)))

    if len(projected) != 4:
        return None
    return order_quad_corners(projected)


def order_quad_by_cardinal(face, obj):
    """South (-Y) bottom, North (+Y) top, West (-X) left, East (+X) right."""
    matrix_world = obj.matrix_world
    projected = []
    for loop in face.loops:
        world_co = matrix_world @ loop.vert.co
        projected.append((loop, float(world_co.x), float(world_co.y)))

    if len(projected) != 4:
        return None

    by_y = sorted(projected, key=lambda item: (item[2], item[1]))
    south_pair = sorted(by_y[:2], key=lambda item: item[1])
    north_pair = sorted(by_y[2:], key=lambda item: item[1])
    return [
        south_pair[0][0],
        south_pair[1][0],
        north_pair[1][0],
        north_pair[0][0],
    ]


def get_shared_vert_indices(face_a, face_b):
    shared = {vert.index for vert in face_a.verts} & {vert.index for vert in face_b.verts}
    if len(shared) == 2:
        return frozenset(shared)
    return None


def get_opposite_edge_vert_indices(face, edge_vert_indices):
    edges = []
    for loop in face.loops:
        edges.append(frozenset({loop.vert.index, loop.link_loop_next.vert.index}))

    for index, edge in enumerate(edges):
        if edge == edge_vert_indices:
            return edges[(index + 2) % 4]
    return None


def loop_for_vert(face, vert):
    for loop in face.loops:
        if loop.vert == vert:
            return loop
    return None


def build_face_adjacency(faces):
    adjacency = {face: [] for face in faces}
    for index, face_a in enumerate(faces):
        for face_b in faces[index + 1:]:
            if get_shared_vert_indices(face_a, face_b) is not None:
                adjacency[face_a].append(face_b)
                adjacency[face_b].append(face_a)
    return adjacency


def walk_face_chain(adjacency):
    nodes = list(adjacency.keys())
    if not nodes:
        return []
    if len(nodes) == 1:
        return nodes

    endpoints = [node for node in nodes if len(adjacency[node]) == 1]
    start = endpoints[0] if endpoints else nodes[0]

    chain = [start]
    previous = None
    current = start

    while True:
        neighbors = [node for node in adjacency[current] if node is not previous]
        if not neighbors:
            break
        nxt = neighbors[0]
        if nxt in chain:
            break
        chain.append(nxt)
        previous, current = current, nxt

    return chain


def find_connected_quad_chains(selected_faces):
    quads = [face for face in selected_faces if len(face.loops) == 4]
    if not quads:
        return []

    adjacency = build_face_adjacency(quads)
    remaining = set(quads)
    chains = []

    while remaining:
        seed = next(iter(remaining))
        component = []
        stack = [seed]
        seen = set()

        while stack:
            face = stack.pop()
            if face in seen:
                continue
            seen.add(face)
            remaining.discard(face)
            component.append(face)
            for neighbor in adjacency.get(face, []):
                if neighbor not in seen:
                    stack.append(neighbor)

        chains.append(walk_face_chain(build_face_adjacency(component)))

    return chains


def order_quad_for_chain(face, prev_face, next_face, obj):
    """
    Order loops so the bottom edge connects to the previous face and the top
    edge connects to the next face in 3D.
    """
    bottom_edge = get_shared_vert_indices(face, prev_face) if prev_face is not None else None
    top_edge = get_shared_vert_indices(face, next_face) if next_face is not None else None

    if bottom_edge is None and top_edge is not None:
        bottom_edge = get_opposite_edge_vert_indices(face, top_edge)
    if top_edge is None and bottom_edge is not None:
        top_edge = get_opposite_edge_vert_indices(face, bottom_edge)

    if bottom_edge is None or top_edge is None:
        return None

    matrix_world = obj.matrix_world
    world_normal = (matrix_world.to_3x3() @ face.normal).normalized()

    if prev_face is not None and next_face is not None:
        chain_dir = face_centroid_world(next_face, obj) - face_centroid_world(prev_face, obj)
    elif next_face is not None:
        chain_dir = face_centroid_world(next_face, obj) - face_centroid_world(face, obj)
    elif prev_face is not None:
        chain_dir = face_centroid_world(face, obj) - face_centroid_world(prev_face, obj)
    else:
        chain_dir = Vector((1.0, 0.0, 0.0))

    chain_dir = chain_dir - world_normal * chain_dir.dot(world_normal)
    if chain_dir.length_squared < 1e-8:
        chain_dir = Vector((1.0, 0.0, 0.0))
    else:
        chain_dir.normalize()

    width_dir = world_normal.cross(chain_dir)
    if width_dir.length_squared < 1e-8:
        width_dir = Vector((0.0, 1.0, 0.0))
    else:
        width_dir.normalize()

    bottom_verts = [vert for vert in face.verts if vert.index in bottom_edge]
    top_verts = [vert for vert in face.verts if vert.index in top_edge]
    if len(bottom_verts) != 2 or len(top_verts) != 2:
        return None

    def world_co(vert):
        return matrix_world @ vert.co

    bottom_verts.sort(key=lambda vert: width_dir.dot(world_co(vert)))
    top_verts.sort(key=lambda vert: width_dir.dot(world_co(vert)))

    ordered_loops = [
        loop_for_vert(face, bottom_verts[0]),
        loop_for_vert(face, bottom_verts[1]),
        loop_for_vert(face, top_verts[1]),
        loop_for_vert(face, top_verts[0]),
    ]
    if any(loop is None for loop in ordered_loops):
        return None
    return ordered_loops


def gridify_quad(ordered_loops, uv):
    """Map an ordered quad to a perfect 1x1 square, matching UVToolkit Gridify."""
    square_uvs = (
        (0.0, 0.0),
        (1.0, 0.0),
        (1.0, 1.0),
        (0.0, 1.0),
    )
    for loop, (u, v) in zip(ordered_loops, square_uvs):
        set_loop_uv(loop, uv, u, v)


def stretch_coords_to_square(coords):
    """Stretch 2D coordinates independently so they fill a unit square."""
    if not coords:
        return []

    xs = [coord[0] for coord in coords]
    ys = [coord[1] for coord in coords]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-8)
    span_y = max(max_y - min_y, 1e-8)

    return [
        ((x - min_x) / span_x, (y - min_y) / span_y)
        for x, y in coords
    ]


def stretch_face_from_view(face, obj, uv, region, region_3d):
    matrix_world = obj.matrix_world
    coords = []
    loops = list(face.loops)
    for loop in loops:
        world_co = matrix_world @ loop.vert.co
        point_2d = project_to_view_2d(world_co, region, region_3d)
        coords.append((float(point_2d.x), float(point_2d.y)))

    square_coords = stretch_coords_to_square(coords)
    for loop, (u, v) in zip(loops, square_coords):
        set_loop_uv(loop, uv, u, v)


def offset_face_uvs(face, uv, offset_u, offset_v):
    for loop in face.loops:
        u, v = loop[uv].uv
        set_loop_uv(loop, uv, u + offset_u, v + offset_v)


def separate_faces_on_grid(faces, uv):
    """Place each 1x1 island on its own grid cell so faces do not share UV space."""
    if not faces:
        return

    columns = max(1, int(len(faces) ** 0.5 + 0.999))
    for index, face in enumerate(faces):
        col = index % columns
        row = index // columns
        offset_face_uvs(face, uv, float(col), float(row))


def fit_island_to_bounds(uv, faces, margin=0.0):
    """
    Scale one island to fill 0-1 UV space using the same math as
    uv.toolkit_fit_to_bounds.
    """
    bbox = get_bbox(uv, faces)
    bbox_center_u, bbox_center_v = calc_bbox_center(bbox)
    bbox_width, bbox_height = get_bbox_size(bbox)
    margin = margin * 2
    pivot = (0.5, 0.5)

    if bbox_height > bbox_width:
        span = bbox_height
    else:
        span = bbox_width

    if span <= 1e-12:
        return

    factor = (1 - margin) / span
    scale = scale_matrix((factor, factor), pivot)
    translate = translate_matrix(pivot[0] - bbox_center_u, pivot[1] - bbox_center_v)
    convolution = np.dot(scale, translate)

    for face in faces:
        for loop in face.loops:
            u, v = loop[uv].uv
            mapped = convolution.dot(np.array([u, v, 1]))[:2]
            set_loop_uv(loop, uv, mapped[0], mapped[1])


def fit_each_face_to_bounds(faces, uv):
    for face in faces:
        fit_island_to_bounds(uv, [face])


def section_pixel_size(texture_size, divisions):
    if divisions <= 0:
        return texture_size
    return max(1, texture_size // divisions)


def section_uv_size(divisions, multiplier=1.0):
    divisions = max(1, divisions)
    return (1.0 / float(divisions)) * float(multiplier)


def uv_to_section(u, v, divisions):
    divisions = max(1, divisions)
    col = int(float(u) * divisions)
    row = int(float(v) * divisions)
    col = max(0, min(divisions - 1, col))
    row = max(0, min(divisions - 1, row))
    return col, row


def section_center_uv(col, row, divisions):
    divisions = max(1, divisions)
    cell = 1.0 / float(divisions)
    return (col + 0.5) * cell, (row + 0.5) * cell


def scale_island_to_span(uv, faces, target_span):
    """Uniformly scale an island around its bbox center to match target_span."""
    bbox = get_bbox(uv, faces)
    center = calc_bbox_center(bbox)
    width, height = get_bbox_size(bbox)
    current_span = max(width, height, 1e-8)
    factor = target_span / current_span
    scale = scale_matrix((factor, factor), center)

    for face in faces:
        for loop in face.loops:
            u, v = loop[uv].uv
            mapped = scale.dot(np.array([u, v, 1]))[:2]
            set_loop_uv(loop, uv, mapped[0], mapped[1])


def translate_island(uv, faces, offset_u, offset_v):
    for face in faces:
        for loop in face.loops:
            u, v = loop[uv].uv
            set_loop_uv(loop, uv, u + offset_u, v + offset_v)


def _uv_close(uv_a, uv_b, tolerance=UV_TOLERANCE):
    return (
        abs(float(uv_a[0]) - float(uv_b[0])) <= tolerance
        and abs(float(uv_a[1]) - float(uv_b[1])) <= tolerance
    )


def loops_share_uv_edge(loop_a, loop_b, uv, tolerance=UV_TOLERANCE):
    uv_a0 = loop_a[uv].uv
    uv_a1 = loop_a.link_loop_next[uv].uv
    uv_b0 = loop_b[uv].uv
    uv_b1 = loop_b.link_loop_next[uv].uv

    same_direction = _uv_close(uv_a0, uv_b0, tolerance) and _uv_close(uv_a1, uv_b1, tolerance)
    flipped = _uv_close(uv_a0, uv_b1, tolerance) and _uv_close(uv_a1, uv_b0, tolerance)
    return same_direction or flipped


def find_uv_islands(bm, uv):
    """UV islands are faces connected by shared UV edges, not mesh seams."""
    islands = []
    visited = set()

    for seed in bm.faces:
        if seed in visited:
            continue

        island_faces = []
        stack = [seed]

        while stack:
            face = stack.pop()
            if face in visited:
                continue
            visited.add(face)
            island_faces.append(face)

            for loop in face.loops:
                radial = loop.link_loop_radial_next
                while radial is not loop:
                    neighbor = radial.face
                    if neighbor not in visited and loops_share_uv_edge(loop, radial, uv):
                        stack.append(neighbor)
                    radial = radial.link_loop_radial_next

        islands.append(island_faces)

    return islands


def get_selected_uv_islands(bm, uv):
    selected = {face for face in bm.faces if face.select}
    if not selected:
        return []

    islands = []
    for island_faces in find_uv_islands(bm, uv):
        if any(face in selected for face in island_faces):
            islands.append(island_faces)
    return islands
