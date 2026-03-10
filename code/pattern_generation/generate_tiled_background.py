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


def generate_unique_tile_color(tile_index, base_hue, hue_range, sat_range, lit_range):
    """Generate a deterministic pseudo-random pastel color for a tile index."""
    # Hash the index for a stable pseudo-random value
    h = hashlib.md5(tile_index.to_bytes(4, 'little')).digest()
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


def draw_tile(tile, image, mask, width, height, offset_coord, scalar, stroke_color, stroke_width, logo_polygons=None, channel_color_override=None):
    fill_color = tile[1][1]
    points = []

    for vertex in tile[0]:
        x = vertex.x * scalar - offset_coord.x * width
        y = vertex.y * scalar + height + offset_coord.y * height
        points.append((x, y))

    if logo_polygons:
        # Fill entire tile with channel color, then overlay logo pieces
        ch_color = channel_color_override if channel_color_override else stroke_color
        fill_polygon(image, mask, width, height, points, ch_color)

        # Recover affine transform from hat_outline to this tile's vertices
        mat = recover_affine(tile[0])

        for logo_poly in logo_polygons:
            screen_pts = []
            for hx, hy in logo_poly:
                tx = mat[0] * hx + mat[1] * hy + mat[2]
                ty = mat[3] * hx + mat[4] * hy + mat[5]
                sx = tx * scalar - offset_coord.x * width
                sy = ty * scalar + height + offset_coord.y * height
                screen_pts.append((sx, sy))
            if len(screen_pts) >= 3:
                fill_polygon(image, mask, width, height, screen_pts, fill_color)
    else:
        fill_polygon(image, mask, width, height, points, fill_color)

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

    # Load logo SVG
    svg_path = config.get("logo_svg", None)
    logo_polygons = None
    if svg_path:
        logo_polygons = load_logo_polygons(svg_path)

    # Unique per-tile color settings (HSL-based)
    unique_colors_cfg = config.get("unique_tile_colors", None)
    base_hue = None
    if unique_colors_cfg:
        # base_hue in config is 0-360 degrees, convert to 0-1
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

    pattern_generator.colors = build_tile_colors(config)

    reset_generator()
    offset_coordinate = seed_to_coordinate(seed)
    pattern_generator.next_generation()

    while True:
        output_image = create_background_image(width, height, background_color)
        coverage_mask = create_coverage_mask(width, height)

        # Accent color setup
        accent_color_cfg = config.get("accent_color", None)
        accent_count = int(config.get("accent_count", 0))
        accent_color = hex_to_bgr(accent_color_cfg) if accent_color_cfg else None

        # Find visible tiles first, then pick accents from those
        accent_indices = set()
        if accent_color and accent_count > 0:
            visible = []
            for ti, tile in enumerate(pattern_generator.vertices_to_draw):
                # Check if tile centroid is within viewport
                cx = sum(v.x for v in tile[0]) / len(tile[0])
                cy = sum(v.y for v in tile[0]) / len(tile[0])
                sx = cx * scalar - offset_coordinate.x * width
                sy = cy * scalar + height + offset_coordinate.y * height
                if 0 <= sx < width and 0 <= sy < height:
                    visible.append(ti)
            if visible:
                for k in range(accent_count):
                    h = hashlib.md5((seed * 1000 + k).to_bytes(8, 'little')).digest()
                    idx = (h[0] | h[1] << 8) % len(visible)
                    accent_indices.add(visible[idx])

        outline_color = config.get("outline_color", None)
        outline_rgb = hex_to_bgr(outline_color) if outline_color else None
        outline_width = int(config.get("outline_width", stroke_width))

        for tile_index, tile in enumerate(pattern_generator.vertices_to_draw):
            channel_override = None
            if tile_index in accent_indices:
                tile = [tile[0], [tile[1][0], accent_color]]
                # Darker variant of accent color for the logo channels
                channel_override = tuple(max(0, c // 2) for c in accent_color)
            elif base_hue is not None:
                tile_color = generate_unique_tile_color(
                    tile_index, base_hue, hue_range, sat_range, lit_range)
                tile = [tile[0], [tile[1][0], tile_color]]

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
                logo_polygons,
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
