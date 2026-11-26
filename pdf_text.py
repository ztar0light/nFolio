"""Store repeated PDF ink shapes at their original page coordinates."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from contextlib import closing
import ctypes as C
import re
import struct
import tempfile
import zlib

from PIL import Image, ImageChops, ImageOps
import folio_compression as comp
import screen_color

BAND = 64
SHADE = [comp.FOUR[v >> 4] for v in range(256)]
GRAY = [s * 85 for s in SHADE]


def colour_patch(image):
    r, g, b = image.split()
    mask = ImageChops.lighter(ImageChops.difference(r, g), ImageChops.difference(g, b))
    if not mask.getbbox():
        return b''
    rgb = image.tobytes()
    patch = bytearray()
    for span in re.finditer(b'[^\x00]+', mask.tobytes()):
        start, end = span.span()
        if len(patch) + 4 + 2 * (end - start) > 12000:
            return None
        words = screen_color.words(rgb[3*start:3*end])
        patch.extend(struct.pack(f'<HH{len(words)}H', start, end-start, *words))
    return bytes(patch)


def positioned_block(stamps, glyphs, patch):
    count = len(stamps)
    if patch is None or 5 * count + len(patch) > 40960:
        return None
    planes = bytearray(5 * count)
    right = bottom = 0
    for i, (idx, x, y) in enumerate(stamps):
        w, h = glyphs.entries[idx][:2]
        delta = x - right
        dx = (delta << 1) ^ (delta >> 31)
        planes[i] = idx & 255
        planes[count + i] = idx >> 8
        planes[2 * count + i] = dx & 255
        planes[3 * count + i] = dx >> 8
        planes[4 * count + i] = (y + h - bottom) & 255
        right, bottom = x + w, y + h
    return b'\x0b' + struct.pack('<HH', count, len(patch)) + zlib.compress(planes + patch, 9)


def runs(projection):
    start = None
    for i, value in enumerate([*projection, 0]):
        if value and start is None:
            start = i
        elif not value and start is not None:
            yield start, i
            start = None


def text_origins(raw, page, w, h):
    # Forms, images and drawings use the ordinary raster path. Character origins
    # merely suggest where to cut a bitmap. The PDF pixels supply all the ink.
    count = raw.FPDFPage_CountObjects(page)
    if count < 1 or any(raw.FPDFPageObj_GetType(raw.FPDFPage_GetObject(page, i)) != raw.FPDF_PAGEOBJ_TEXT
                        for i in range(count)):
        return None
    rows = defaultdict(set)
    x, y = C.c_double(), C.c_double()
    px, py = C.c_int(), C.c_int()
    with closing(page.get_textpage()) as text:
        total = text.count_chars()
        if not 1 <= total <= 100000:
            return None
        for i in range(total):
            if (raw.FPDFText_GetCharOrigin(text, i, x, y) and
                    raw.FPDF_PageToDevice(page, 0, 0, w, h, 0, x.value, y.value, px, py)):
                rows[py.value].add(px.value)
    return rows or None


def glyph_entry(image, mask, left, right):
    box = mask.crop((left, 0, right, image.height)).getbbox()
    if box is None:
        return None
    x, y, end, bottom = box
    glyph = image.crop((left + x, y, left + end, bottom))
    w, h = glyph.size
    if w > 255 or h > 120:
        return None
    if w & 1:
        padded = Image.new('L', (w + 1, h), 255)
        padded.paste(glyph)
        glyph = padded
    pixels = glyph.tobytes()
    entry = bytes((w, h)) + bytes((a & 240) | (b >> 4) for a, b in zip(pixels[::2], pixels[1::2]))
    return entry, left + x, y


def raster(image, gray, quality):
    r, g, b = image.split()
    if not gray and (ImageChops.difference(r, g).getbbox() or ImageChops.difference(g, b).getbbox()):
        return screen_color.encode(image, 'smallest', quality)
    pixels = image.convert('L').point(SHADE).tobytes()
    packed = bytes((a << 6) | (b << 4) | (c << 2) | d
                   for a, b, c, d in zip(pixels[::4], pixels[1::4], pixels[2::4], pixels[3::4]))
    return b'\x06' + zlib.compress(packed, 9)


class PdfTextPack:
    def __init__(self, width, scratch, glyphs, gray, quality):
        self.width = width
        self.scratch = scratch
        self.glyphs = glyphs
        self.gray = gray
        self.quality = quality
        self.file = scratch.enter_context(tempfile.TemporaryFile())
        self.offsets = [0]
        self.buffer = Image.new('RGB', (width, BAND), 'white')
        self.used = 0
        self.stamps = []
        self.touched = False
        self.used_glyphs = False

    def page_bands(self, image, origins):
        base = self.used
        bands = [[] for _ in range((base + image.height + BAND - 1) // BAND)]
        def invalidate(y, end):
            for i in range((base + y) // BAND, (base + end - 1) // BAND + 1):
                bands[i] = None
        ink = ImageOps.invert(image.convert('L').point(GRAY))
        baselines = sorted(origins)
        for top, bottom in runs(ink.getprojection()[1]):
            if bottom - top > 120:
                invalidate(top, bottom)
                continue
            first, last = (base + top) // BAND, (base + bottom - 1) // BAND
            if all(bands[i] is None for i in range(first, last + 1)):
                continue
            near = baselines[bisect_left(baselines, top - 2):bisect_right(baselines, bottom + 5)]
            cuts = sorted({x for y in near for x in origins[y]})
            mask = ink.crop((0, top, self.width, bottom))
            line = ImageOps.invert(mask)
            for left, right in runs(mask.getprojection()[0]):
                edges = [left, *cuts[bisect_right(cuts, left):bisect_left(cuts, right)], right]
                for x0, x1 in zip(edges, edges[1:]):
                    glyph = glyph_entry(line, mask, x0, x1)
                    if glyph is None:
                        invalidate(top, bottom)
                        continue
                    entry, x, dy = glyph
                    idx = self.glyphs.intern(entry)
                    if idx is None:
                        invalidate(top, bottom)
                        continue
                    y = base + top + dy
                    for band in range(y // BAND, (y + entry[1] - 1) // BAND + 1):
                        if bands[band] is not None:
                            bands[band].append((idx, x, y - band * BAND))
        return bands

    def add_page(self, raw, page, image, fit_height):
        h = fit_height * (self.width // 320)
        origins = text_origins(raw, page, self.width, h)
        # Nearest sampling for the rounding difference between an integer PDF
        # bitmap and the 2x layout keeps the original hinted strokes reusable.
        method = Image.Resampling.NEAREST if origins and abs(image.height - h) <= 1 and abs(image.width - self.width) <= 1 else Image.Resampling.LANCZOS
        target = image if image.size == (self.width, h) else image.resize((self.width, h), method)
        bands = self.page_bands(target, origins) if origins else None
        y = band = 0
        while y < h:
            rows = min(BAND - self.used, h - y)
            if origins or self.used or rows < BAND:
                self.buffer.paste(target.crop((0, y, self.width, y + rows)), (0, self.used))
            if bands is None or bands[band] is None:
                self.stamps = None
            elif self.stamps is not None:
                self.stamps.extend(bands[band])
            self.touched |= bool(origins)
            self.used += rows
            y += rows
            if self.used == BAND:
                self.flush()
                band += 1

    def flush(self):
        if self.touched:
            if self.used < BAND:
                self.buffer.paste('white', (0, self.used, self.width, BAND))
            block = raster(self.buffer, self.gray, self.quality)
            if self.stamps and len(self.stamps) <= 8192:
                patch = b'' if self.gray else colour_patch(self.buffer)
                stamp = positioned_block(self.stamps, self.glyphs, patch)
                if stamp and len(stamp) < len(block):
                    block = stamp
                    self.used_glyphs = True
            self.file.write(block)
        self.offsets.append(self.file.tell())
        self.used = 0
        self.stamps = []
        self.touched = False

    def finish(self, source, original_atlas, thumbnail_bytes):
        if self.used:
            self.flush()
        if not self.used_glyphs or original_atlas:
            return source, original_atlas
        assert len(self.offsets) == len(source.offsets)
        merged = comp.StripStorage(self.scratch, 'smallest')
        for i, (a, b) in enumerate(zip(self.offsets, self.offsets[1:])):
            if a < b:
                self.file.seek(a)
                block = self.file.read(b - a)
            else:
                # A copied raster reference must resolve against the original
                # stream. The new stream builds its own back-references.
                x, y = source.offsets[i:i + 2]
                source.file.seek(x)
                block = source.file.read(y - x)
                if block[0] == comp.BAND_REFERENCE:
                    ref = struct.unpack_from('<I', block, 1)[0]
                    x, y = source.offsets[ref:ref + 2]
                    source.file.seek(x)
                    block = source.file.read(y - x)
            merged.write(block)
        merged.end()
        atlas = comp.compress_atlas(self.glyphs.encode(), 'smallest')
        if merged.offsets[-1] + len(atlas) + thumbnail_bytes < source.offsets[-1]:
            return merged, atlas
        return source, original_atlas
