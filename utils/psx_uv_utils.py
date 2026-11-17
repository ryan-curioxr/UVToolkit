import math
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


def unique_verts_from_faces(faces):
    verts = []
    seen = set()
    for face in faces:
        for vert in face.verts:
            if vert.index in seen:
                continue
            seen.add(vert.index)
            verts.append(vert)
    return verts


def order_verts_by_view(verts, obj, region, region_3d):
    matrix_world = obj.matrix_world
    projected = []
    for vert in verts:
        world_co = matrix_world @ vert.co
        point_2d = project_to_view_2d(world_co, region, region_3d)
        projected.append((vert, float(point_2d.x), float(point_2d.y)))

    if len(projected) != 4:
        return None
    return order_quad_corners(projected)


def order_verts_by_cardinal(verts, obj):
    """South (-Y) bottom, North (+Y) top, West (-X) left, East (+X) right."""
    matrix_world = obj.matrix_world
    projected = []
    for vert in verts:
        world_co = matrix_world @ vert.co
        projected.append((vert, float(world_co.x), float(world_co.y)))

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


def order_quad_by_view(face, obj, region, region_3d):
    verts = order_verts_by_view([loop.vert for loop in face.loops], obj, region, region_3d)
    if verts is None:
        return None
    return [loop_for_vert(face, vert) for vert in verts]


def order_quad_by_cardinal(face, obj):
    verts = order_verts_by_cardinal([loop.vert for loop in face.loops], obj)
    if verts is None:
        return None
    ordered = [loop_for_vert(face, vert) for vert in verts]
    if any(loop is None for loop in ordered):
        return None
    return ordered


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


def piece_vert_indices(piece):
    return {vert.index for face in piece for vert in face.verts}


def piece_centroid_world(piece, obj):
    verts = unique_verts_from_faces(piece)
    if not verts:
        return Vector((0.0, 0.0, 0.0))
    matrix_world = obj.matrix_world
    center = Vector((0.0, 0.0, 0.0))
    for vert in verts:
        center += matrix_world @ vert.co
    return center / len(verts)


def piece_normal(piece, obj):
    matrix = obj.matrix_world.to_3x3()
    total = Vector((0.0, 0.0, 0.0))
    for face in piece:
        total += matrix @ face.normal
    if total.length_squared < 1e-12:
        return Vector((0.0, 0.0, 1.0))
    return total.normalized()


def _internal_diagonal(piece):
    if len(piece) != 2:
        return None
    return get_shared_vert_indices(piece[0], piece[1])


def _quad_pair_score(face_a, face_b):
    shared = get_shared_vert_indices(face_a, face_b)
    if shared is None:
        return -1.0

    verts = unique_verts_from_faces((face_a, face_b))
    if len(verts) != 4:
        return -1.0

    lengths = []
    for face in (face_a, face_b):
        for loop in face.loops:
            edge = frozenset({loop.vert.index, loop.link_loop_next.vert.index})
            if edge == shared:
                continue
            lengths.append((loop.vert.co - loop.link_loop_next.vert.co).length)

    if len(lengths) != 4:
        return -1.0

    max_length = max(lengths)
    min_length = min(lengths)
    if max_length < 1e-12:
        return -1.0

    planarity = abs(face_a.normal.normalized().dot(face_b.normal.normalized()))
    return (min_length / max_length) * planarity


def pair_triangles_into_quads(faces):
    """Pair selected triangles that share an edge into quad-shaped pieces."""
    triangles = [face for face in faces if len(face.loops) == 3]
    if len(triangles) < 2:
        return []

    adjacency = {face: [] for face in triangles}
    for index, face_a in enumerate(triangles):
        for face_b in triangles[index + 1:]:
            if get_shared_vert_indices(face_a, face_b) is not None:
                adjacency[face_a].append(face_b)
                adjacency[face_b].append(face_a)

    unused = set(triangles)
    pairs = []
    while unused:
        seed = min(
            unused,
            key=lambda face: (len([n for n in adjacency[face] if n in unused]), face.index),
        )
        unused.discard(seed)
        candidates = [neighbor for neighbor in adjacency[seed] if neighbor in unused]
        if not candidates:
            continue
        partner = max(candidates, key=lambda neighbor: _quad_pair_score(seed, neighbor))
        unused.discard(partner)
        pairs.append((seed, partner))
    return pairs


def collect_unwrap_pieces(selected_faces):
    """Group faces into unwrap pieces: quads, triangle-pairs, or leftover faces."""
    pairs = pair_triangles_into_quads(selected_faces)
    paired = {face for pair in pairs for face in pair}
    pieces = [pair for pair in pairs]
    for face in selected_faces:
        if face not in paired:
            pieces.append((face,))
    return pieces


def pieces_share_boundary_edge(piece_a, piece_b):
    shared = piece_vert_indices(piece_a) & piece_vert_indices(piece_b)
    if len(shared) != 2:
        return False
    shared_edge = frozenset(shared)
    if _internal_diagonal(piece_a) == shared_edge:
        return False
    if _internal_diagonal(piece_b) == shared_edge:
        return False
    return True


def is_quad_like_piece(piece):
    return len(unique_verts_from_faces(piece)) == 4 and (
        (len(piece) == 1 and len(piece[0].loops) == 4)
        or len(piece) == 2
    )


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
    pieces = [piece for piece in collect_unwrap_pieces(selected_faces) if is_quad_like_piece(piece)]
    if not pieces:
        return []

    adjacency = {piece: [] for piece in pieces}
    for index, piece_a in enumerate(pieces):
        for piece_b in pieces[index + 1:]:
            if pieces_share_boundary_edge(piece_a, piece_b):
                adjacency[piece_a].append(piece_b)
                adjacency[piece_b].append(piece_a)

    remaining = set(pieces)
    chains = []

    while remaining:
        seed = next(iter(remaining))
        component = []
        stack = [seed]
        seen = set()

        while stack:
            piece = stack.pop()
            if piece in seen:
                continue
            seen.add(piece)
            remaining.discard(piece)
            component.append(piece)
            for neighbor in adjacency.get(piece, []):
                if neighbor not in seen:
                    stack.append(neighbor)

        component_adjacency = {piece: [] for piece in component}
        for index, piece_a in enumerate(component):
            for piece_b in component[index + 1:]:
                if pieces_share_boundary_edge(piece_a, piece_b):
                    component_adjacency[piece_a].append(piece_b)
                    component_adjacency[piece_b].append(piece_a)
        chains.append(walk_face_chain(component_adjacency))

    return chains


def order_quad_for_chain(face, prev_face, next_face, obj):
    """
    Order loops so the bottom edge connects to the previous face and the top
    edge connects to the next face in 3D.
    """
    prev_piece = (prev_face,) if prev_face is not None else None
    next_piece = (next_face,) if next_face is not None else None
    ordered_verts = order_piece_for_chain((face,), prev_piece, next_piece, obj)
    if ordered_verts is None:
        return None
    ordered_loops = [loop_for_vert(face, vert) for vert in ordered_verts]
    if any(loop is None for loop in ordered_loops):
        return None
    return ordered_loops


def order_piece_for_chain(piece, prev_piece, next_piece, obj):
    """
    Order the 4 verts of a quad or triangle-pair so bottom connects to the
    previous piece and top connects to the next piece.
    """
    verts = unique_verts_from_faces(piece)
    if len(verts) != 4:
        return None

    vert_indices = {vert.index for vert in verts}
    verts_by_index = {vert.index: vert for vert in verts}

    def shared_edge(other):
        if other is None:
            return None
        shared = vert_indices & piece_vert_indices(other)
        if len(shared) != 2:
            return None
        return frozenset(shared)

    bottom_edge = shared_edge(prev_piece)
    top_edge = shared_edge(next_piece)

    if bottom_edge is None and top_edge is not None:
        bottom_edge = frozenset(vert_indices - top_edge)
    if top_edge is None and bottom_edge is not None:
        top_edge = frozenset(vert_indices - bottom_edge)

    if bottom_edge is None or top_edge is None:
        return None

    matrix_world = obj.matrix_world
    world_normal = piece_normal(piece, obj)

    if prev_piece is not None and next_piece is not None:
        chain_dir = piece_centroid_world(next_piece, obj) - piece_centroid_world(prev_piece, obj)
    elif next_piece is not None:
        chain_dir = piece_centroid_world(next_piece, obj) - piece_centroid_world(piece, obj)
    elif prev_piece is not None:
        chain_dir = piece_centroid_world(piece, obj) - piece_centroid_world(prev_piece, obj)
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

    bottom_verts = [verts_by_index[index] for index in bottom_edge]
    top_verts = [verts_by_index[index] for index in top_edge]
    if len(bottom_verts) != 2 or len(top_verts) != 2:
        return None

    def world_co(vert):
        return matrix_world @ vert.co

    bottom_verts.sort(key=lambda vert: width_dir.dot(world_co(vert)))
    top_verts.sort(key=lambda vert: width_dir.dot(world_co(vert)))

    return [
        bottom_verts[0],
        bottom_verts[1],
        top_verts[1],
        top_verts[0],
    ]


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


def gridify_piece(piece, ordered_verts, uv):
    """Map a quad or triangle-pair onto one shared 1x1 square by vertex."""
    square_uvs = (
        (0.0, 0.0),
        (1.0, 0.0),
        (1.0, 1.0),
        (0.0, 1.0),
    )
    uv_for_vert = {
        vert.index: square_uvs[index]
        for index, vert in enumerate(ordered_verts)
    }
    for face in piece:
        for loop in face.loops:
            coords = uv_for_vert.get(loop.vert.index)
            if coords is None:
                continue
            set_loop_uv(loop, uv, coords[0], coords[1])


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


def separate_islands_on_grid(islands, uv):
    """Place each island on its own grid cell so islands do not share UV space."""
    if not islands:
        return

    columns = max(1, int(len(islands) ** 0.5 + 0.999))
    for index, faces in enumerate(islands):
        col = index % columns
        row = index // columns
        for face in faces:
            offset_face_uvs(face, uv, float(col), float(row))


def separate_faces_on_grid(faces, uv):
    """Place each 1x1 island on its own grid cell so faces do not share UV space."""
    separate_islands_on_grid([[face] for face in faces], uv)


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


def fit_each_island_to_bounds(islands, uv):
    for faces in islands:
        fit_island_to_bounds(uv, faces)


def fit_each_face_to_bounds(faces, uv):
    fit_each_island_to_bounds([[face] for face in faces], uv)


def section_pixel_size(texture_size, divisions):
    if divisions <= 0:
        return texture_size
    return max(1, texture_size // divisions)


def section_uv_size(divisions, multiplier=1.0):
    divisions = max(1, divisions)
    return (1.0 / float(divisions)) * float(multiplier)


def uv_to_section(u, v, divisions):
    """Map a UV point to the section whose center is nearest."""
    divisions = max(1, divisions)
    cell = 1.0 / float(divisions)

    def nearest_index(coord):
        index = (float(coord) / cell) - 0.5
        nearest = int(math.floor(index + 0.5))
        return max(0, min(divisions - 1, nearest))

    return nearest_index(u), nearest_index(v)


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


def scale_selected_islands_to_section(context):
    """
    Scale selected UV islands to the current Grid Sections size.
    Same behavior as uv.toolkit_psx_scale_to_section.
    """
    settings = context.scene.uv_toolkit_psx
    target_span = section_uv_size(
        settings.grid_divisions,
        settings.section_scale_multiplier,
    )
    pixel_size = int(round(settings.texture_size * target_span))
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

    return scaled_count, target_span, pixel_size


def translate_island(uv, faces, offset_u, offset_v):
    for face in faces:
        for loop in face.loops:
            u, v = loop[uv].uv
            set_loop_uv(loop, uv, u + offset_u, v + offset_v)


def snap_island_to_section_center(uv, faces, divisions):
    bbox = get_bbox(uv, faces)
    center_u, center_v = calc_bbox_center(bbox)
    col, row = uv_to_section(center_u, center_v, divisions)
    target_u, target_v = section_center_uv(col, row, divisions)
    translate_island(uv, faces, target_u - center_u, target_v - center_v)


def snap_selected_islands_to_section_centers(context):
    settings = context.scene.uv_toolkit_psx
    divisions = settings.grid_divisions
    snapped_count = 0

    for obj in context.objects_in_mode_unique_data:
        if obj.type != 'MESH':
            continue

        bm = bmesh.from_edit_mesh(obj.data)
        uv = ensure_uv_layer(bm)
        islands = get_selected_uv_islands(bm, uv)
        if not islands:
            continue

        for island_faces in islands:
            snap_island_to_section_center(uv, island_faces, divisions)
            snapped_count += 1

        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    return snapped_count


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
    """
    UV islands among selected faces only.

    Unselected neighbors are ignored, even if they share UV coordinates.
    That prevents stacked atlas islands from dragging surrounding faces.
    """
    selected = {face for face in bm.faces if face.select}
    if not selected:
        return []

    islands = []
    visited = set()
    for seed in selected:
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
                    if (
                        neighbor in selected
                        and neighbor not in visited
                        and loops_share_uv_edge(loop, radial, uv)
                    ):
                        stack.append(neighbor)
                    radial = radial.link_loop_radial_next

        islands.append(island_faces)
    return islands
