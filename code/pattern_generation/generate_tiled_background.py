#!/usr/bin/env python3

import argparse
import colorsys
import hashlib
import json
import math
import re
import struct
import zlib
from itertools import cycle
from pathlib import Path

import pattern_generator
from geometry import Vector, hat_outline

TILE_LABELS = ("H1", "H", "T", "P", "F")

SQRT3 = 1.7320508075688772

# Hat outline bounds (precomputed)
HAT_MIN_X = min(v.x for v in hat_outline)
HAT_MAX_X = max(v.x for v in hat_outline)
HAT_MIN_Y = min(v.y for v in hat_outline)
HAT_MAX_Y = max(v.y for v in hat_outline)
HAT_WIDTH = HAT_MAX_X - HAT_MIN_X
HAT_HEIGHT = HAT_MAX_Y - HAT_MIN_Y

# Coordinates inferred from the TokenTek SVG and mapped into hat space.
LOGO_NODE_COORDS = {
    "left_top": (0.013810, -1.732051),
    "left_mid": (0.013810, -0.000586),
    "bottom": (0.976144, 1.791805),
    "upper_center": (1.509343, -0.892794),
    "center": (1.509343, 0.888147),
    "mid_right": (2.021893, -0.000586),
    "lower_right": (2.992609, 1.749893),
    "right_top": (3.068664, -1.629214),
    "right_mid": (3.068664, -0.000586),
}

LOGO_EDGES = (
    ("left_top", "left_mid"),
    ("left_mid", "upper_center"),
    ("left_mid", "center"),
    ("bottom", "center"),
    ("upper_center", "mid_right"),
    ("center", "mid_right"),
    ("center", "lower_right"),
    ("mid_right", "right_mid"),
    ("right_top", "right_mid"),
)

LOGO_NODE_RADIUS_MULTIPLIERS = {
    "left_top": 0.9,
    "left_mid": 1.0,
    "bottom": 0.95,
    "upper_center": 1.0,
    "center": 1.2,
    "mid_right": 1.0,
    "lower_right": 0.95,
    "right_top": 0.9,
    "right_mid": 1.0,
}


# --- SVG path parsing ---

def _tokenize_path(d):
    return re.findall(r'[A-Za-z]|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?', d)


def _cubic_bezier(p0, p1, p2, p3, n=10):
    points = []
    for i in range(1, n + 1):
        t = i / n
        mt = 1 - t
        x = mt**3*p0[0] + 3*mt**2*t*p1[0] + 3*mt*t**2*p2[0] + t**3*p3[0]
        y = mt**3*p0[1] + 3*mt**2*t*p1[1] + 3*mt*t**2*p2[1] + t**3*p3[1]
        points.append((x, y))
    return points


def _parse_path_to_polygons(d):
    tokens = _tokenize_path(d)
    polygons = []
    current = []
    cx, cy = 0.0, 0.0
    sx, sy = 0.0, 0.0
    i = 0

    while i < len(tokens):
        cmd = tokens[i]
        if not cmd.isalpha():
            i += 1
            continue
        i += 1

        def num():
            nonlocal i
            v = float(tokens[i])
            i += 1
            return v

        if cmd == 'M':
            if current:
                polygons.append(current)
            cx, cy = num(), num()
            sx, sy = cx, cy
            current = [(cx, cy)]
            while i < len(tokens) and not tokens[i].isalpha():
                cx, cy = num(), num()
                current.append((cx, cy))
        elif cmd == 'm':
            if current:
                polygons.append(current)
            cx += num(); cy += num()
            sx, sy = cx, cy
            current = [(cx, cy)]
            while i < len(tokens) and not tokens[i].isalpha():
                cx += num(); cy += num()
                current.append((cx, cy))
        elif cmd == 'L':
            while i < len(tokens) and not tokens[i].isalpha():
                cx, cy = num(), num()
                current.append((cx, cy))
        elif cmd == 'l':
            while i < len(tokens) and not tokens[i].isalpha():
                cx += num(); cy += num()
                current.append((cx, cy))
        elif cmd == 'H':
            while i < len(tokens) and not tokens[i].isalpha():
                cx = num()
                current.append((cx, cy))
        elif cmd == 'h':
            while i < len(tokens) and not tokens[i].isalpha():
                cx += num()
                current.append((cx, cy))
        elif cmd == 'V':
            while i < len(tokens) and not tokens[i].isalpha():
                cy = num()
                current.append((cx, cy))
        elif cmd == 'v':
            while i < len(tokens) and not tokens[i].isalpha():
                cy += num()
                current.append((cx, cy))
        elif cmd == 'C':
            while i < len(tokens) and not tokens[i].isalpha():
                x1, y1 = num(), num()
                x2, y2 = num(), num()
                x3, y3 = num(), num()
                current.extend(_cubic_bezier((cx, cy), (x1, y1), (x2, y2), (x3, y3)))
                cx, cy = x3, y3
        elif cmd == 'c':
            while i < len(tokens) and not tokens[i].isalpha():
                dx1, dy1 = num(), num()
                dx2, dy2 = num(), num()
                dx3, dy3 = num(), num()
                current.extend(_cubic_bezier(
                    (cx, cy), (cx+dx1, cy+dy1), (cx+dx2, cy+dy2), (cx+dx3, cy+dy3)))
                cx, cy = cx + dx3, cy + dy3
        elif cmd in ('Z', 'z'):
            cx, cy = sx, sy
            if current:
                polygons.append(current)
                current = []

    if current:
        polygons.append(current)
    return polygons


def load_logo_polygons(svg_path):
    """Parse SVG and convert paths to hat_outline coordinate space (mirrored horizontally)."""
    svg_text = Path(svg_path).read_text()
    path_data = re.findall(r'<path[^>]*\bd="([^"]+)"', svg_text)

    all_svg_polys = []
    for d in path_data:
        all_svg_polys.extend(_parse_path_to_polygons(d))

    all_pts = [pt for poly in all_svg_polys for pt in poly]
    svg_min_x = min(p[0] for p in all_pts)
    svg_max_x = max(p[0] for p in all_pts)
    svg_min_y = min(p[1] for p in all_pts)
    svg_max_y = max(p[1] for p in all_pts)
    svg_w = svg_max_x - svg_min_x
    svg_h = svg_max_y - svg_min_y
    scale = ((svg_w / HAT_WIDTH) + (svg_h / HAT_HEIGHT)) / 2

    hat_polys = []
    for poly in all_svg_polys:
        hat_polys.append([
            (HAT_MAX_X - (x - svg_min_x) / scale,
             (y - svg_min_y) / scale + HAT_MIN_Y)
            for x, y in poly
        ])
    return hat_polys


def recover_affine(tile_vertices):
    """Recover the affine transform from hat_outline space to tile vertex space."""
    # hat_outline[0] = (0, 0), hat_outline[7] = (4, 0), hat_outline[9] = (3, sqrt3)
    c = tile_vertices[0].x
    f = tile_vertices[0].y
    a = (tile_vertices[7].x - c) / 4.0
    d = (tile_vertices[7].y - f) / 4.0
    b = (tile_vertices[9].x - a * 3.0 - c) / SQRT3
    e = (tile_vertices[9].y - d * 3.0 - f) / SQRT3
    return (a, b, c, d, e, f)


def generate_unique_tile_color(tile_index, base_hue, hue_range, sat_range, lit_range, salt=0):
    """Generate a deterministic pseudo-random pastel color for a tile index."""
    # Hash the tile index with an optional salt so we can try alternate colors.
    h = hashlib.md5(f"{tile_index}:{salt}".encode("utf-8")).digest()
    r1 = h[0] / 255.0
    r2 = h[1] / 255.0
    r3 = h[2] / 255.0

    hue = (base_hue + (r1 - 0.5) * hue_range) % 1.0
    sat = sat_range[0] + r2 * (sat_range[1] - sat_range[0])
    lit = lit_range[0] + r3 * (lit_range[1] - lit_range[0])

    r, g, b = colorsys.hls_to_rgb(hue, lit, sat)
    return (int(r * 255), int(g * 255), int(b * 255))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Render a tiled Einstein background from a JSON config.",
    )
    parser.add_argument(
        "config",
        nargs="?",
        default="config/tiled_background.json",
        help="Path to the JSON config file.",
    )
    return parser.parse_args()


def load_config(config_path):
    with open(config_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def hex_to_bgr(value):
    normalized = value.strip().lstrip("#")
    if len(normalized) != 6:
        raise ValueError(f"Expected a 6-digit hex color, got '{value}'.")

    red = int(normalized[0:2], 16)
    green = int(normalized[2:4], 16)
    blue = int(normalized[4:6], 16)
    return (red, green, blue)


def build_tile_colors(config):
    palette = config.get("palette", [])
    tile_colors = config.get("tile_colors", {})

    if not palette and not tile_colors:
        raise ValueError("Config must define 'palette' or 'tile_colors'.")

    color_cycle = cycle(palette or ["#8F8A7D"])
    resolved = {}

    for label in TILE_LABELS:
        color = tile_colors.get(label, next(color_cycle))
        resolved[label] = [color, hex_to_bgr(color)]

    return resolved


def build_tile_adjacency(tiles, precision=6):
    adjacency = [set() for _ in tiles]
    edge_to_tiles = {}

    def point_key(vertex):
        return (round(vertex.x, precision), round(vertex.y, precision))

    for tile_index, tile in enumerate(tiles):
        vertices = tile[0]
        for vertex_index in range(len(vertices)):
            start = point_key(vertices[vertex_index])
            end = point_key(vertices[(vertex_index + 1) % len(vertices)])
            edge = (start, end) if start <= end else (end, start)
            edge_to_tiles.setdefault(edge, []).append(tile_index)

    for tile_indices in edge_to_tiles.values():
        if len(tile_indices) < 2:
            continue
        for left_index in range(len(tile_indices)):
            for right_index in range(left_index + 1, len(tile_indices)):
                a = tile_indices[left_index]
                b = tile_indices[right_index]
                adjacency[a].add(b)
                adjacency[b].add(a)

    return adjacency


def dedupe_colors(colors):
    unique = []
    seen = set()

    for color in colors:
        if color in seen:
            continue
        seen.add(color)
        unique.append(color)

    return unique


def derive_color_variant(base_color, tile_index, salt):
    h = hashlib.md5(f"{tile_index}:{salt}:{base_color}".encode("utf-8")).digest()
    red, green, blue = (channel / 255.0 for channel in base_color)
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)

    hue = (hue + ((h[0] / 255.0) - 0.5) * 0.08) % 1.0
    saturation = min(1.0, max(0.05, saturation + ((h[1] / 255.0) - 0.5) * 0.18))
    lightness = min(0.82, max(0.18, lightness + ((h[2] / 255.0) - 0.5) * 0.42))

    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return (int(r * 255), int(g * 255), int(b * 255))


def assign_tile_fill_colors(tiles, adjacency, config, palette_colors):
    unique_colors_cfg = config.get("unique_tile_colors", None)
    assigned = [None] * len(tiles)
    palette_colors = dedupe_colors(palette_colors)
    tile_order = sorted(range(len(tiles)), key=lambda idx: (-len(adjacency[idx]), idx))

    if unique_colors_cfg:
        base_hue = unique_colors_cfg.get("base_hue", 80) / 360.0
        hue_range = unique_colors_cfg.get("hue_range", 30) / 360.0
        sat_range = (
            unique_colors_cfg.get("saturation_min", 8) / 100.0,
            unique_colors_cfg.get("saturation_max", 20) / 100.0,
        )
        lit_range = (
            unique_colors_cfg.get("lightness_min", 40) / 100.0,
            unique_colors_cfg.get("lightness_max", 62) / 100.0,
        )
    else:
        base_hue = hue_range = None
        sat_range = lit_range = None

    for tile_index in tile_order:
        used_neighbor_colors = {
            assigned[neighbor_index]
            for neighbor_index in adjacency[tile_index]
            if assigned[neighbor_index] is not None
        }

        if unique_colors_cfg:
            for salt in range(128):
                color = generate_unique_tile_color(
                    tile_index, base_hue, hue_range, sat_range, lit_range, salt=salt
                )
                if color not in used_neighbor_colors:
                    assigned[tile_index] = color
                    break
            continue

        default_color = tiles[tile_index][1][1]
        candidate_colors = dedupe_colors([default_color] + palette_colors)

        selected = None
        for color in candidate_colors:
            if color not in used_neighbor_colors:
                selected = color
                break

        if selected is None:
            for salt in range(128):
                color = derive_color_variant(default_color, tile_index, salt)
                if color not in used_neighbor_colors:
                    selected = color
                    break

        assigned[tile_index] = selected or default_color

    return assigned


def select_accent_tiles(visible_indices, adjacency, seed, accent_count):
    if accent_count <= 0 or not visible_indices:
        return set()

    ranked_visible = []
    for tile_index in visible_indices:
        h = hashlib.md5(f"{seed}:{tile_index}".encode("utf-8")).digest()
        score = int.from_bytes(h[:4], "little")
        ranked_visible.append((score, tile_index))

    ranked_visible.sort()

    selected = set()
    for _, tile_index in ranked_visible:
        if any(neighbor in selected for neighbor in adjacency[tile_index]):
            continue
        selected.add(tile_index)
        if len(selected) >= accent_count:
            break

    return selected


def seed_to_coordinate(seed):
    found_layer = False
    start_of_layer = 1

    layer = 0
    while not found_layer:
        if seed >= start_of_layer and seed < start_of_layer + (4 + 8 * layer):
            found_layer = True
        else:
            start_of_layer = start_of_layer + (4 + 8 * layer)
            layer += 1

    number_of_coords_in_layer = 4 + layer * 8
    output_coord = Vector(0, 0)

    if seed >= start_of_layer and seed <= start_of_layer + layer:
        output_coord.y = layer
        output_coord.x = seed - start_of_layer
    elif (
        seed < start_of_layer + number_of_coords_in_layer
        and seed >= start_of_layer + number_of_coords_in_layer - layer - 1
    ):
        output_coord.y = layer
        output_coord.x = seed - (start_of_layer + number_of_coords_in_layer)
    elif seed > start_of_layer + layer * 3 and seed <= start_of_layer + layer * 5 + 2:
        output_coord.y = -layer - 1
        output_coord.x = (start_of_layer + layer * 4 + 1) - seed
    elif seed > start_of_layer + layer and seed <= start_of_layer + layer * 3:
        output_coord.x = layer
        output_coord.y = (start_of_layer + layer * 2) - seed
    elif (
        seed > start_of_layer + layer * 5 + 2
        and seed < start_of_layer + number_of_coords_in_layer - layer - 1
    ):
        output_coord.x = -layer - 1
        output_coord.y = seed - (start_of_layer + layer * 6 + 3)

    return output_coord


def reset_generator():
    pattern_generator.vertices_to_draw.clear()
    pattern_generator.tiles = [
        pattern_generator.H_init(),
        pattern_generator.T_init(),
        pattern_generator.P_init(),
        pattern_generator.F_init(),
    ]
    pattern_generator.level = 1


def create_background_image(width, height, background_color):
    return bytearray(background_color * (width * height))


def create_coverage_mask(width, height):
    return bytearray(width * height)


def set_pixel(image, mask, width, height, x, y, color):
    if x < 0 or x >= width or y < 0 or y >= height:
        return

    offset = (y * width + x) * 3
    image[offset : offset + 3] = bytes(color)
    mask[y * width + x] = 1


def draw_brush(image, mask, width, height, x, y, color, stroke_width):
    radius = max(0, stroke_width - 1)
    for y_pos in range(y - radius, y + radius + 1):
        for x_pos in range(x - radius, x + radius + 1):
            set_pixel(image, mask, width, height, x_pos, y_pos, color)


def draw_line(image, mask, width, height, start, end, color, stroke_width):
    x1 = int(round(start[0]))
    y1 = int(round(start[1]))
    x2 = int(round(end[0]))
    y2 = int(round(end[1]))

    dx = abs(x2 - x1)
    dy = -abs(y2 - y1)
    step_x = 1 if x1 < x2 else -1
    step_y = 1 if y1 < y2 else -1
    error = dx + dy

    while True:
        draw_brush(image, mask, width, height, x1, y1, color, stroke_width)
        if x1 == x2 and y1 == y2:
            break

        doubled_error = error * 2
        if doubled_error >= dy:
            error += dy
            x1 += step_x
        if doubled_error <= dx:
            error += dx
            y1 += step_y


def draw_filled_circle(image, mask, width, height, center, radius, color):
    cx = int(round(center[0]))
    cy = int(round(center[1]))
    radius = max(1, int(round(radius)))
    radius_sq = radius * radius

    for y in range(cy - radius, cy + radius + 1):
        dy = y - cy
        for x in range(cx - radius, cx + radius + 1):
            dx = x - cx
            if dx * dx + dy * dy <= radius_sq:
                set_pixel(image, mask, width, height, x, y, color)


def draw_round_line(image, mask, width, height, start, end, color, line_width):
    radius = max(1.0, line_width / 2.0)
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)

    if length == 0:
        draw_filled_circle(image, mask, width, height, start, radius, color)
        return

    steps = max(1, int(math.ceil(length / max(0.75, radius * 0.18))))
    for step in range(steps + 1):
        t = step / steps
        x = start[0] + dx * t
        y = start[1] + dy * t
        draw_filled_circle(image, mask, width, height, (x, y), radius, color)


def fill_polygon(image, mask, width, height, points, color):
    if not points:
        return

    min_y = max(0, math.floor(min(point[1] for point in points)))
    max_y = min(height - 1, math.ceil(max(point[1] for point in points)))
    edge_count = len(points)

    for y in range(min_y, max_y + 1):
        scan_y = y + 0.5
        intersections = []

        for index in range(edge_count):
            x1, y1 = points[index]
            x2, y2 = points[(index + 1) % edge_count]

            if y1 == y2:
                continue

            if scan_y < min(y1, y2) or scan_y >= max(y1, y2):
                continue

            ratio = (scan_y - y1) / (y2 - y1)
            intersections.append(x1 + ratio * (x2 - x1))

        intersections.sort()

        for index in range(0, len(intersections), 2):
            if index + 1 >= len(intersections):
                break

            start_x = max(0, math.ceil(intersections[index]))
            end_x = min(width - 1, math.floor(intersections[index + 1]))
            for x in range(start_x, end_x + 1):
                set_pixel(image, mask, width, height, x, y, color)


def transform_logo_nodes(tile_vertices, width, height, offset_coord, scalar):
    mat = recover_affine(tile_vertices)
    screen_nodes = {}

    for name, (hx, hy) in LOGO_NODE_COORDS.items():
        tx = mat[0] * hx + mat[1] * hy + mat[2]
        ty = mat[3] * hx + mat[4] * hy + mat[5]
        sx = tx * scalar - offset_coord.x * width
        sy = ty * scalar + height + offset_coord.y * height
        screen_nodes[name] = (sx, sy)

    return screen_nodes


def draw_logo_network(image, mask, width, height, screen_nodes, color, scalar):
    line_width = max(2, int(round(scalar * 0.07)))
    base_radius = max(line_width * 1.45, scalar * 0.11)

    for start_name, end_name in LOGO_EDGES:
        draw_round_line(
            image,
            mask,
            width,
            height,
            screen_nodes[start_name],
            screen_nodes[end_name],
            color,
            line_width,
        )

    for name, center in screen_nodes.items():
        draw_filled_circle(
            image,
            mask,
            width,
            height,
            center,
            base_radius * LOGO_NODE_RADIUS_MULTIPLIERS[name],
            color,
        )


def draw_tile(tile, image, mask, width, height, offset_coord, scalar, stroke_color, stroke_width, draw_logo=False, channel_color_override=None):
    fill_color = tile[1][1]
    points = []

    for vertex in tile[0]:
        x = vertex.x * scalar - offset_coord.x * width
        y = vertex.y * scalar + height + offset_coord.y * height
        points.append((x, y))

    fill_polygon(image, mask, width, height, points, fill_color)

    if draw_logo:
        logo_color = channel_color_override if channel_color_override else stroke_color
        screen_nodes = transform_logo_nodes(tile[0], width, height, offset_coord, scalar)
        draw_logo_network(image, mask, width, height, screen_nodes, logo_color, scalar)

    for index in range(len(points)):
        draw_line(
            image,
            mask,
            width,
            height,
            points[index],
            points[(index + 1) % len(points)],
            stroke_color,
            stroke_width,
        )


def write_png(path, width, height, image):
    def chunk(chunk_type, payload):
        return (
            struct.pack("!I", len(payload))
            + chunk_type
            + payload
            + struct.pack("!I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
        )

    rows = []
    row_size = width * 3
    for row_index in range(height):
        start = row_index * row_size
        rows.append(b"\x00" + bytes(image[start : start + row_size]))

    png_data = b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(b"".join(rows), level=9)),
            chunk(b"IEND", b""),
        ]
    )

    path.write_bytes(png_data)


def render_image(config):
    width = int(config.get("image_width", 2400))
    height = int(config.get("image_height", 1600))
    scalar = int(config.get("scalar", 48))
    seed = int(config.get("seed", 6))
    output_file = Path(config.get("output_file", "output/tiled_background.png"))
    background_color = hex_to_bgr(config.get("background_color", "#FFFFFF"))
    stroke_color = hex_to_bgr(config.get("stroke_color", "#000000"))
    stroke_width = int(config.get("stroke_width", 2))
    empty_pixel_tolerance = int(config.get("empty_pixel_tolerance", 3))

    draw_logo = bool(config.get("logo_svg", None))

    pattern_generator.colors = build_tile_colors(config)
    palette_colors = dedupe_colors(
        [fill_data[1] for fill_data in pattern_generator.colors.values()]
    )

    reset_generator()
    offset_coordinate = seed_to_coordinate(seed)
    pattern_generator.next_generation()

    while True:
        output_image = create_background_image(width, height, background_color)
        coverage_mask = create_coverage_mask(width, height)

        adjacency = build_tile_adjacency(pattern_generator.vertices_to_draw)
        fill_colors = assign_tile_fill_colors(
            pattern_generator.vertices_to_draw,
            adjacency,
            config,
            palette_colors,
        )

        # Accent color setup
        accent_color_cfg = config.get("accent_color", None)
        accent_count = int(config.get("accent_count", 0))
        accent_color = hex_to_bgr(accent_color_cfg) if accent_color_cfg else None

        # Find visible tiles first, then pick accents from those
        visible = []
        if accent_color and accent_count > 0:
            for ti, tile in enumerate(pattern_generator.vertices_to_draw):
                # Check if tile centroid is within viewport
                cx = sum(v.x for v in tile[0]) / len(tile[0])
                cy = sum(v.y for v in tile[0]) / len(tile[0])
                sx = cx * scalar - offset_coordinate.x * width
                sy = cy * scalar + height + offset_coordinate.y * height
                if 0 <= sx < width and 0 <= sy < height:
                    visible.append(ti)
        accent_indices = select_accent_tiles(visible, adjacency, seed, accent_count)

        outline_color = config.get("outline_color", None)
        outline_rgb = hex_to_bgr(outline_color) if outline_color else None
        outline_width = int(config.get("outline_width", stroke_width))

        for tile_index, tile in enumerate(pattern_generator.vertices_to_draw):
            channel_override = None
            tile_fill_color = fill_colors[tile_index]
            if tile_index in accent_indices:
                tile_fill_color = accent_color
                # Darker variant of accent color for the logo channels
                channel_override = tuple(max(0, c // 2) for c in accent_color)
            tile = [tile[0], [tile[1][0], tile_fill_color]]

            draw_tile(
                tile,
                output_image,
                coverage_mask,
                width,
                height,
                offset_coordinate,
                scalar,
                stroke_color,
                stroke_width,
                draw_logo,
                channel_override,
            )

            # Draw hat outline on top
            if outline_rgb:
                points = []
                for vertex in tile[0]:
                    x = vertex.x * scalar - offset_coordinate.x * width
                    y = vertex.y * scalar + height + offset_coordinate.y * height
                    points.append((x, y))
                for idx in range(len(points)):
                    draw_line(
                        output_image, coverage_mask, width, height,
                        points[idx], points[(idx + 1) % len(points)],
                        outline_rgb, outline_width,
                    )

        empty_pixels = coverage_mask.count(0)
        if empty_pixels <= empty_pixel_tolerance:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            write_png(output_file, width, height, output_image)
            return output_file

        pattern_generator.next_generation()


def main():
    args = parse_args()
    config = load_config(args.config)
    output_file = render_image(config)
    print(output_file)


if __name__ == "__main__":
    main()
