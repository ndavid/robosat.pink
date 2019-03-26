"""Slippy Map Tiles.
   See: https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames
"""

import io
import os
import re
import glob
import warnings

import numpy as np
from PIL import Image
from rasterio import open as rasterio_open

import csv
import mercantile

from robosat_pink.colors import make_palette, complementary_palette

warnings.simplefilter("ignore", UserWarning)  # To prevent rasterio NotGeoreferencedWarning


def tile_pixel_to_location(tile, dx, dy):
    """Converts a pixel in a tile to lon/lat coordinates."""

    assert 0 <= dx <= 1 and 0 <= dy <= 1, "x and y offsets must be in [0, 1]"

    w, s, e, n = mercantile.bounds(tile)

    def lerp(a, b, c):
        return a + c * (b - a)

    return lerp(w, e, dx), lerp(s, n, dy)  # lon, lat


def tiles_from_slippy_map(root):
    """Loads files from an on-disk slippy map dir."""

    root = os.path.expanduser(root)
    paths = glob.glob(os.path.join(root, "[0-9]*/[0-9]*/[0-9]*.*"))

    for path in paths:
        tile = re.match(os.path.join(root, "(?P<z>[0-9]+)/(?P<x>[0-9]+)/(?P<y>[0-9]+).+"), path)
        if not tile:
            continue

        yield mercantile.Tile(int(tile["x"]), int(tile["y"]), int(tile["z"])), path


def tile_from_slippy_map(root, x, y, z):
    """Retrieve a single tile from a slippy map dir."""

    path = glob.glob(os.path.join(os.path.expanduser(root), z, x, y + ".*"))
    if not path:
        return None

    return mercantile.Tile(x, y, z), path[0]


def tiles_from_csv(path):
    """Retrieve tiles from a line-delimited csv file."""

    with open(os.path.expanduser(path)) as fp:
        reader = csv.reader(fp)

        for row in reader:
            if not row:
                continue

            yield mercantile.Tile(*map(int, row))


def tile_image_from_file(path, bands=None):
    """Return a multiband image numpy array, from an image file path, or None."""

    try:
        raster = rasterio_open(os.path.expanduser(path))
    except:
        return None

    image = None
    for i in raster.indexes if bands is None else bands:
        data_band = raster.read(i)
        data_band = data_band.reshape(data_band.shape[0], data_band.shape[1], 1)  # H, W -> H, W, C
        image = np.concatenate((image, data_band), axis=2) if image is not None else data_band

    return image


def tile_label_from_file(path):
    """Return a numpy array, from a label file path, or None."""

    try:
        return np.array(Image.open(os.path.expanduser(path)))
    except:
        return None


def tile_label_to_file(root, tile, colors, label):
    """ Write a label tile on disk. """

    out_path = os.path.join(os.path.expanduser(root), str(tile.z), str(tile.x))
    os.makedirs(out_path, exist_ok=True)

    out = Image.fromarray(label, mode="P")
    out.putpalette(complementary_palette(make_palette(colors[0], colors[1])))
    out.save(os.path.join(out_path, "{}.png".format(tile.y)), optimize=True)


def tile_image_from_url(requests_session, url, timeout=10):
    """Fetch a tile image using HTTP, and return it or None """

    try:
        resp = requests_session.get(url, timeout=timeout)
        resp.raise_for_status()
        return io.BytesIO(resp.content)

    except Exception:
        return None


def tile_image_buffer(tile, path, overlap=64):
    """Buffers a tile image adding borders on all sides based on adjacent tiles, if presents, or with zeros."""

    def tile_image_adjacent(root, tile, dx, dy):
        path = tile_from_slippy_map(root, int(tile.x) + dx, int(tile.y) + dy, int(tile.z))
        return None if not path else tile_image_from_file(path)

    root = re.sub("^(.+)(/[0-9]+/[0-9]+/[0-9]+.+)$", r"\1", path)

    # 3x3 matrix (upper, center, bottom) x (left, center, right)
    ul = tile_image_adjacent(root, tile, -1, -1)
    uc = tile_image_adjacent(root, tile, +0, -1)
    ur = tile_image_adjacent(root, tile, +1, -1)
    cl = tile_image_adjacent(root, tile, -1, +0)
    cc = tile_image_adjacent(root, tile, +0, +0)
    cr = tile_image_adjacent(root, tile, +1, +0)
    bl = tile_image_adjacent(root, tile, -1, +1)
    bc = tile_image_adjacent(root, tile, +0, +1)
    br = tile_image_adjacent(root, tile, +1, +1)

    o = overlap
    oo = overlap * 2
    ts = cc.shape[1]
    assert 0 <= overlap <= ts, "Overlap value can't be either negative or bigger than tile_size"

    img = np.zeros((ts + oo, ts + oo, 3)).astype(np.uint8)

    # fmt:off
    img[0:o,        0:o,        :] = ul[-o:ts, -o:ts, :] if ul is not None else np.zeros((o,   o, 3)).astype(np.uint8)
    img[0:o,        o:ts+o,     :] = uc[-o:ts,  0:ts, :] if uc is not None else np.zeros((o,  ts, 3)).astype(np.uint8)
    img[0:o,        ts+o:ts+oo, :] = ur[-o:ts,   0:o, :] if ur is not None else np.zeros((o,   o, 3)).astype(np.uint8)
    img[o:ts+o,     0:o,        :] = cl[0:ts,  -o:ts, :] if cl is not None else np.zeros((ts,  o, 3)).astype(np.uint8)
    img[o:ts+o,     o:ts+o,     :] = cc                  if cc is not None else np.zeros((ts, ts, 3)).astype(np.uint8)
    img[o:ts+o,     ts+o:ts+oo, :] = cr[0:ts,    0:o, :] if cr is not None else np.zeros((ts,  o, 3)).astype(np.uint8)
    img[ts+o:ts+oo, 0:o,        :] = bl[0:o,   -o:ts, :] if bl is not None else np.zeros((o,   o, 3)).astype(np.uint8)
    img[ts+o:ts+oo, o:ts+o,     :] = bc[0:o,    0:ts, :] if bc is not None else np.zeros((o,  ts, 3)).astype(np.uint8)
    img[ts+o:ts+oo, ts+o:ts+oo, :] = br[0:o,     0:o, :] if br is not None else np.zeros((o,   o, 3)).astype(np.uint8)
    # fmt:on

    return img
