#!/usr/bin/env python3

import argparse
import json
import math
import struct
import zlib
from itertools import cycle
from pathlib import Path

import pattern_generator
from geometry import Vector

TILE_LABELS = ("H1", "H", "T", "P", "F")


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


def draw_tile(tile, image, mask, width, height, offset_coord, scalar, stroke_color, stroke_width):
    fill_color = tile[1][1]
    points = []

    for vertex in tile[0]:
        x = vertex.x * scalar - offset_coord.x * width
        y = vertex.y * scalar + height + offset_coord.y * height
        points.append((x, y))

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

    pattern_generator.colors = build_tile_colors(config)

    reset_generator()
    offset_coordinate = seed_to_coordinate(seed)
    pattern_generator.next_generation()

    while True:
        output_image = create_background_image(width, height, background_color)
        coverage_mask = create_coverage_mask(width, height)

        for tile in pattern_generator.vertices_to_draw:
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
