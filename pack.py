#!/usr/bin/env python3
"""Prepare a Folio book for a colour TI-Nspire. Python 3.10+ and Pillow."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import struct
import sys
import tempfile
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
import zlib
from html.parser import HTMLParser

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps
except ImportError:
    raise SystemExit('Folio needs Pillow. Install it with: python -m pip install Pillow') from None

import folio_compression as comp
import pdf_nav
import pdf_text
import screen_color

WIDTH, BAND = 320, 64
LABEL_W, LABEL_H = 264, 20
HEADER = struct.Struct('<8s16s8I')
DEFAULT_FONT = Path(__file__).resolve().parent / 'fonts' / 'DejaVuSerif.ttf'
PICTURES = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff'}
PAGE_FOLDERS = {'pages', 'images', 'scans'}
RESAMPLE = Image.Resampling.LANCZOS
COVER_SIZE = (84, 118)


def cover_thumbnail(image):
    im = ImageOps.contain(image.convert('RGB'), COVER_SIZE, RESAMPLE)
    tile = Image.new('RGB', COVER_SIZE, (247, 247, 231))
    tile.paste(im, ((tile.width - im.width) // 2, (tile.height - im.height) // 2))
    out = io.BytesIO()
    tile.save(out, 'JPEG', quality=66, optimize=True)
    return out.getvalue()


def natural(name):
    return [(int(part) if part.isdecimal() else part.casefold()) for part in re.split(r'(\d+)', str(name))]


def visible(name):
    return all(not p.startswith('.') and p != '__MACOSX' for p in PurePosixPath(str(name)).parts)


def prepared_book(path):
    return path.name.lower().endswith('.folio.tns') and path.is_file()


def default_output(source, profile):
    if not source.name:
        return source/'Pages.folio.tns'
    name = source.name if source.is_dir() else source.stem
    if prepared_book(source):
        name = source.name[:-len('.folio.tns')] + '-' + profile
    return source.with_name(name + '.folio.tns')


def label_bits(name, font):
    canvas = Image.new('1', (LABEL_W, LABEL_H), 0)
    draw = ImageDraw.Draw(canvas)
    name = ' '.join(name.split()) or 'Untitled'
    if font.getlength(name) > LABEL_W - 2:
        lo, hi = 0, len(name)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.getlength(name[:mid] + '…') <= LABEL_W - 2:
                lo = mid
            else:
                hi = mid - 1
        name = name[:lo] + '…'
    draw.text((1, LABEL_H // 2), name, anchor='lm', font=font, fill=1)
    return canvas.tobytes()


def encode_band(im, quality, gray):
    r, g, b = im.split()
    monochrome = gray or (not ImageChops.difference(r, g).getbbox() and not ImageChops.difference(g, b).getbbox())
    if monochrome:
        lum = im.convert('L').tobytes()
        pixels = bytes((a & 0xF0) | (b >> 4) for a, b in zip(lum[::2], lum[1::2]))
        return b'\x00' + zlib.compress(pixels, 8)
    out = io.BytesIO()
    im.save(out, 'JPEG', quality=quality, subsampling=0, optimize=True)
    jpeg = b'\x01' + out.getvalue()
    # Flat coloured lettering and panel backgrounds can be smaller losslessly.
    if im.getcolors(32) is not None:
        q = im.quantize(colors=32, dither=Image.Dither.NONE)
        count = 1 + q.getextrema()[1]
        pal = q.getpalette()[:count * 3]
        rgb565 = [((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3) for r, g, b in zip(pal[::3], pal[1::3], pal[2::3])]
        indexed = bytes([2, count]) + struct.pack(f'<{count}H', *rgb565) + zlib.compress(q.tobytes(), 8)
        if len(indexed) < len(jpeg):
            return indexed
    return jpeg


class Glyphs:
    """Share repeated shapes after the PC font engine has laid out each line."""
    def __init__(self):
        self.lookup = {}
        self.entries = []
        self.size = 0

    def intern(self, entry):
        index = self.lookup.get(entry)
        if index is None:
            if self.size + len(entry) + 4 > 750000 or len(self.entries) >= 16000:
                return None
            index = len(self.entries)
            self.lookup[entry] = index
            self.entries.append(entry)
            self.size += len(entry) + 4
        return index

    def spans(self, line):
        # Blank columns separate ink without changing kerning, ligatures or shaping.
        mask = line.point([255 if value < 240 else 0 for value in range(256)])
        occupied = list(mask.getprojection()[0]) + [0]
        start, stamps = None, []
        for x, ink in enumerate(occupied):
            if ink and start is None:
                start = x
            elif not ink and start is not None:
                top, bottom = mask.crop((start, 0, x, line.height)).getbbox()[1::2]
                w, h = x-start, bottom-top
                if w > 255 or h > 120:
                    return None
                glyph = line.crop((start, top, x, bottom))
                if w % 2:
                    padded = Image.new('L', (w+1, h), 255)
                    padded.paste(glyph, (0,0)); glyph = padded
                lum = glyph.tobytes()
                entry = bytes([w,h]) + bytes((a&240)|(b>>4) for a,b in zip(lum[::2],lum[1::2]))
                index = self.intern(entry)
                if index is None:
                    return None
                stamps.append((index, start, top, h)); start = None
        return stamps

    def encode(self):
        if not self.entries: return b''
        offsets = [4+4*(len(self.entries)+1)]
        for entry in self.entries: offsets.append(offsets[-1]+len(entry))
        raw = struct.pack('<I',len(self.entries)) + struct.pack(f'<{len(offsets)}I',*offsets) + b''.join(self.entries)
        return struct.pack('<I',len(raw)) + zlib.compress(raw,9)


class BandStream(comp.StripStorage):
    def __init__(self, scale, quality, gray, scratch, glyphs, profile, target_quality):
        super().__init__(scratch, profile)
        self.reference_only=False
        self.native_reference_bytes=0
        self.target_quality = target_quality
        self.standard = scratch.enter_context(tempfile.TemporaryFile())
        self.scale, self.quality, self.gray = scale, quality, gray
        self.width = WIDTH * scale
        self.glyphs = glyphs
        self.stamps, self.text_only = [], True
        self.used = self.height = 0
        self.buffer = Image.new('RGB', (self.width, BAND), 'white')

    def add(self, image, *, prose=False, blank=False):
        stamps = self.glyphs.spans(image) if prose else ([] if blank else None)
        assert image.width == self.width
        self.height += image.height
        if self.height > 0x3fffffff:
            raise ValueError('Book exceeds the supported height. Pack fewer chapters.')
        y = 0
        while y < image.height:
            n = min(BAND - self.used, image.height - y)
            if stamps is None:
                self.text_only = False
            elif self.text_only:
                self.stamps.extend((i,x,top-y+self.used) for i,x,top,h in stamps if top < y+n and top+h > y)
            self.buffer.paste(image.crop((0, y, self.width, y + n)), (0, self.used))
            self.used += n
            y += n
            if self.used == BAND:
                self.flush()

    def flush(self):
        if not self.used:
            return
        if self.used < BAND:
            self.buffer.paste('white', (0, self.used, self.width, BAND))
        encoded = encode_band(self.buffer, self.quality, self.gray)
        if self.text_only and self.stamps:
            commands = b"".join(struct.pack("<HHb", *stamp) for stamp in self.stamps)
            candidate = b"\x03" + struct.pack("<H",len(self.stamps)) + zlib.compress(commands,9)
            if len(candidate) < len(encoded): encoded = candidate
        self.standard.write(encoded)
        if self.reference_only:
            self.offsets.append(0)
        elif encoded[0] in (1,2):
            best,reference=screen_color.encode_with_reference(self.buffer,self.profile,self.color_quality)
            self.native_reference_bytes+=reference
            self.write(best)
        else:
            self.native_reference_bytes+=len(encoded)
            self.write(comp.optimize_block(encoded,self.width,self.profile,self.target_quality,self.buffer))
        self.stamps, self.text_only = [], True
        self.used = 0


def clusters(word):
    """Keep combining marks, variation selectors and joined glyphs together."""
    group = ''
    for char in word:
        joined = unicodedata.category(char).startswith('M') or char == '\u200d' or group.endswith('\u200d')
        if group and not joined:
            yield group
            group = ''
        group += char
    if group:
        yield group


def wrap(text, font, width):
    line = ''
    for word in text.split():
        candidate = f'{line} {word}' if line else word
        if font.getlength(candidate) <= width:
            line = candidate
            continue
        if line:
            yield line
        if font.getlength(word) <= width:
            line = word
            continue
        line = ''
        for piece in clusters(word):
            if line and font.getlength(line + piece) > width:
                yield line
                line = ''
            line += piece
    if line:
        yield line


class Book:
    def __init__(self, args, scratch):
        self.args = args
        self.glyphs = Glyphs()
        source = args.input
        suffix = source.suffix.lower()
        if source.is_dir():
            artwork = args.pages_from_stdin or not any(p.suffix.lower()=='.txt' for p in source.iterdir())
        else:
            artwork = suffix in PICTURES | {'.cbz','.zip','.pdf'}
        illustrated_epub = False
        if suffix=='.epub' and not source.is_dir() and not args.fit_only:
            with zipfile.ZipFile(source) as archive:
                illustrated_epub=any(PurePosixPath(n).suffix.lower() in PICTURES for n in archive.namelist())
        self.master = artwork and not args.fit_only
        detail = args.detail or self.master or illustrated_epub
        self.pdf_nav = b''
        self.streams = [BandStream(i, args.quality or 82, args.gray, scratch, self.glyphs,
                                   args.compression, args.quality or comp.JPEG_QUALITY[args.compression]) for i in ([1,2] if detail else [1])]
        self.streams[0].reference_only=self.master
        for stream in self.streams:
            stream.color_quality=args.quality
        self.pdf_text = None
        self.cover = b''
        self.epub_cover = False
        if suffix == '.pdf' and not source.is_dir() and args.compression == 'smallest':
            self.pdf_text = pdf_text.PdfTextPack(self.streams[-1].width, scratch, Glyphs(), args.gray, args.quality)
        def font(size):
            return ImageFont.truetype(str(args.font), size, index=args.font_index)
        self.fonts = {scale: (font(args.font_size * scale), font((args.font_size + 4) * scale)) for scale in (1, 2)}
        self.label_font = font(15)
        self.checked_chars, self.missing_chars = set(), set()
        missing = self.fonts[1][0].getmask(chr(0x10ffff))
        self.missing_shape = (missing.size, bytes(missing))
        self.chapters, self.pages = [], []
        self.wide_ends = {}
        self.input_bytes = 0
        self.chapter_no = 0
        self.in_range = False
        self.title = args.title or args.input.stem
        self._pending_name = None

    @property
    def height(self):
        return self.streams[0].height

    def chapter(self, name):
        self.chapter_no += 1
        self.in_range = self.args.chapters[0] <= self.chapter_no <= self.args.chapters[1]
        self._pending_name = name or f'Chapter {self.chapter_no}'
        return self.in_range

    def content(self):
        if self._pending_name is not None:
            self.chapters.append((self.height, len(self.pages), label_bits(self._pending_name, self.label_font)))
            self._pending_name = None
            self.mark_page()

    def mark_page(self):
        if not self.pages or self.pages[-1] != self.height:
            self.pages.append(self.height)

    def gap(self, n):
        for s in self.streams:
            s.add(Image.new('L', (s.width, n * s.scale), 255), blank=True)

    def paragraph(self, text, heading=False):
        text = ' '.join(text.split())
        if not text or not self.in_range:
            return
        for char in set(text) - self.checked_chars:
            if ord(char)>126 and unicodedata.category(char)[0] in 'LMNPS':
                mask = self.fonts[1][0].getmask(char)
                if (mask.size, bytes(mask)) == self.missing_shape: self.missing_chars.add(char)
            self.checked_chars.add(char)
        self.content()
        font = self.fonts[1][int(heading)]
        ascent, descent = font.getmetrics()
        step = ascent + descent + 3
        self.gap(10 if heading else 3)
        for line in wrap(text, font, WIDTH - 24):
            if self.height - self.pages[-1] + step > 232:
                self.mark_page()
            for s in self.streams:
                scale = s.scale
                lineim = Image.new('L', (s.width, step * scale), 255)
                ImageDraw.Draw(lineim).text((12 * scale, (ascent + 1) * scale), line,
                    font=self.fonts[scale][int(heading)], anchor='ls', fill=12)
                s.add(lineim, prose=True)
        self.gap(6 if heading else 4)

    def picture(self, source, allow_upscale=True):
        if not self.in_range:
            return
        with Image.open(source) as original:
            original.seek(0)
            im = ImageOps.exif_transpose(original)
            if 'A' in im.getbands() or 'transparency' in im.info:
                rgba = im.convert('RGBA')
                im = Image.new('RGB', rgba.size, 'white')
                im.paste(rgba, mask=rgba.getchannel('A'))
            else:
                im = im.convert('RGB')
            self.image(im, allow_upscale)

    def image(self, im, allow_upscale=True):
        if self.pdf_text is not None and not self.height:
            self.cover = cover_thumbnail(im)
        self.content()
        self.mark_page()
        w = WIDTH if allow_upscale else min(WIDTH, im.width)
        h = max(1, round(im.height * w / im.width))
        if im.width > im.height and w == WIDTH:
            self.wide_ends[len(self.pages)-1] = self.height+h
        for s in self.streams:
            dw, dh = w * s.scale, h * s.scale
            # Resize bounded output chunks, using source coordinates from the full image.
            for y in range(0, dh, 512):
                end = min(dh, y + 512)
                method=Image.Resampling.NEAREST if im.width<=WIDTH and dw>im.width else RESAMPLE
                piece = im.resize((dw, end-y), method, box=(0, im.height*y/dh, im.width, im.height*end/dh))
                if dw != s.width:
                    padded = Image.new('RGB', (s.width, piece.height), 'white')
                    padded.paste(piece, ((s.width-dw)//2, 0))
                    piece = padded
                s.add(piece)

    def finish(self, path):
        if not self.chapters or not self.height:
            raise ValueError('No readable content in the selected chapters.')
        if len(self.chapters) > 8192 or len(self.pages) > 500000:
            raise ValueError('Too many chapters or pages. Split the book using --chapters.')
        for s in self.streams:
            s.flush()
            s.end()
            assert s.height == self.height * s.scale
        label = label_bits(self.title, self.label_font)
        chapter_pos = HEADER.size + len(label)
        chapter_data = b''.join(struct.pack('<II', y, page) + bits for y, page, bits in self.chapters)
        page_pos = chapter_pos + len(chapter_data)
        page_data = struct.pack(f'<{len(self.pages)}I', *self.pages)
        table_pos = page_pos + len(page_data)
        indices = [table_pos, 0]
        if len(self.streams) == 2:
            indices[1] = table_pos + len(self.streams[0].offsets) * 4
        standard_atlas = self.glyphs.encode()
        atlas = comp.compress_atlas(standard_atlas,self.args.compression)
        ends = self.pages[1:] + [self.height]
        tagged_pages = struct.pack(f'<{len(self.pages)}I', *(y | (comp.WIDE if self.wide_ends.get(i) == ends[i] else 0)
                                                            for i,y in enumerate(self.pages)))
        # Content and layout identify a book across renames. The display title is cosmetic.
        fields = (self.height, len(self.chapters), len(self.pages), len(self.streams), chapter_pos, page_pos, *indices)
        digest = hashlib.sha256(struct.pack('<8I', *fields) + chapter_data + page_data + standard_atlas)
        for s in self.streams:
            s.standard.seek(0)
            for block in iter(lambda: s.standard.read(1 << 20), b''):
                digest.update(block)
        # The stored 640-wide master supplies both views of an image book.
        # Keep the previous single-level identity for automatic detail on image sources.
        if self.master and not self.args.detail:
            legacy_fields=(self.height,len(self.chapters),len(self.pages),1,chapter_pos,page_pos,table_pos,0)
            digest=hashlib.sha256(struct.pack('<8I',*legacy_fields)+chapter_data+page_data+standard_atlas)
            self.streams[0].standard.seek(0)
            for block in iter(lambda:self.streams[0].standard.read(1<<20),b''):
                digest.update(block)
        stores=self.streams[-1:] if self.master else self.streams
        if self.master:
            fields=(*fields[:3],0x102,*fields[4:7],0)
        nav_bytes=len(self.pdf_nav)+8
        self.standard_bytes = table_pos+4*sum(len(st.offsets) for st in stores)+len(standard_atlas)+sum(st.native_reference_bytes for st in stores)+nav_bytes
        v4 = False
        if self.pdf_text is not None:
            base = stores[-1]
            stores[-1], atlas = self.pdf_text.finish(base, atlas, len(self.cover) + 8)
            v4 = stores[-1] is not base
        if self.epub_cover:
            v4 = True
            self.standard_bytes += len(self.cover) + 8
        write_pack(path,self.args.compression,digest.digest()[:16],fields,label,chapter_data,tagged_pages,atlas,stores,self.pdf_nav,v4,self.cover)


def write_pack(path, profile, identity, fields, label, chapters, pages, atlas, stores, navigation, v4=False, cover=b''):
    payload = fields[6] + sum(len(v.offsets)*4 for v in stores) + len(atlas)
    tables = []
    for store in stores:
        if payload + store.offsets[-1] > 0x7fffffff:
            raise ValueError('Pack is over 2 GiB. Use --chapters to split it.')
        tables.append(struct.pack(f'<{len(store.offsets)}I', *(payload+n for n in store.offsets)))
        payload += store.offsets[-1]
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.folio-', delete=False) as f:
        temp = Path(f.name)
    try:
        with temp.open('wb') as out:
            out.write(HEADER.pack((comp.V4 if v4 else comp.V3)[comp.PROFILES.index(profile)], identity, *fields))
            for block in (label,chapters,pages,*tables,atlas): out.write(block)
            for store in stores:
                store.file.seek(0); shutil.copyfileobj(store.file,out)
            if v4:
                out.write(cover)
                out.write(pdf_nav.FOOTER.pack(len(cover), b'CVR4'))
            if out.tell()+len(navigation)+8>0x7fffffff:
                raise ValueError('Book exceeds 2 GiB. Select fewer chapters.')
            out.write(navigation)
            out.write(pdf_nav.FOOTER.pack(len(navigation),b'FNAV'))
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def text_chapters(book, path):
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    current, blocks, pending = path.stem, [], []
    def flush_paragraph():
        if pending:
            blocks.append(' '.join(pending)); pending.clear()
    def emit():
        if blocks and book.chapter(current):
            book.paragraph(current, True)
            for p in blocks:
                book.paragraph(p)
    for line in lines:
        line = line.strip()
        heading = re.match(r'^#{1,3}\s+(.+)$', line) or re.fullmatch(r'((?:chapter|prologue|epilogue)\b.{0,68})', line, re.I)
        if heading and (line.startswith('#') or not pending):
            flush_paragraph(); emit(); blocks.clear(); current = heading.group(1)
        elif line:
            pending.append(line)
        else:
            flush_paragraph()
    flush_paragraph(); emit()


def comic(book, names, opener, title, *, unwrap_chapters=False):
    # Preserve book order even for mixed root pages and chapter folders.
    last_group = None
    for name in sorted(names, key=natural):
        p = PurePosixPath(str(name))
        if not visible(p) or p.suffix.lower() not in PICTURES:
            continue
        group = p.parent
        if unwrap_chapters:
            while group.parent.name and group.name.casefold() in PAGE_FOLDERS:
                group = group.parent
        if group != last_group:
            book.chapter(group.name or title)
            last_group = group
        if book.in_range:
            with opener(name) as src:
                book.picture(src)


def cbz(book, path):
    with zipfile.ZipFile(path) as archive:
        comic(book, archive.namelist(), archive.open, path.stem)


def local_ref(base, href):
    u = urllib.parse.urlsplit(href)
    if u.scheme or u.netloc or not u.path:
        return None
    name = posixpath.normpath(posixpath.join(posixpath.dirname(base), urllib.parse.unquote(u.path)))
    return None if name.startswith(('/', '../')) else name


class XHTML(HTMLParser):
    boundaries = {'p','div','section','article','h1','h2','h3','h4','h5','h6','li','blockquote','tr','br','hr','figure','figcaption'}
    hidden = {'head','script','style','nav','rt','rp'}
    def __init__(self, anchors):
        super().__init__(convert_charrefs=True)
        self.anchors = anchors
        self.parts, self.events = [], []
        self.ignored = self.heading = 0

    def flush(self):
        text = ' '.join(''.join(self.parts).split())
        if text:
            self.events.append(('heading' if self.heading else 'paragraph', text))
        self.parts.clear()

    def handle_starttag(self, tag, attrs):
        tag = tag.split(':')[-1]
        if tag in self.hidden:
            self.ignored += 1
        if self.ignored:
            return
        a = dict(attrs)
        anchor = a.get('id') or (a.get('name') if tag == 'a' else None)
        if anchor in self.anchors:
            self.flush(); self.events.append(('anchor',self.anchors[anchor]))
        if tag in self.boundaries:
            self.flush()
            if re.fullmatch(r'h[1-6]', tag): self.heading += 1
        if tag in {'img','image'}:
            href = a.get('src') or a.get('href') or a.get('xlink:href')
            if href:
                self.flush(); self.events.append(('image', href))

    def handle_endtag(self, tag):
        tag = tag.split(':')[-1]
        if tag in self.hidden:
            self.ignored = max(0, self.ignored-1); return
        if not self.ignored and tag in self.boundaries:
            self.flush()
            if re.fullmatch(r'h[1-6]', tag): self.heading = max(0, self.heading-1)

    def handle_data(self, text):
        if not self.ignored:
            self.parts.append(text)


def epub_toc(z, opf, package, manifest):
    targets = {}
    def add(base, href, title):
        if not href or not title: return
        name = local_ref(base, href) or (base if href.startswith('#') else None)
        if name:
            anchor = urllib.parse.unquote(urllib.parse.urlsplit(href).fragment)
            targets.setdefault(name, {}).setdefault(anchor, ' '.join(title.split()))
    for item in opf.findall('./{*}manifest/{*}item'):
        if 'nav' not in item.get('properties','').split(): continue
        base = local_ref(package, item.get('href',''))
        if not base: continue
        root = ET.fromstring(z.read(base))
        for nav in root.findall('.//{*}nav'):
            types = nav.get('{http://www.idpf.org/2007/ops}type','').split()
            if 'toc' not in types and nav.get('role') != 'doc-toc': continue
            for link in nav.findall('.//{*}a'):
                add(base,link.get('href'),''.join(link.itertext()))
    if targets: return targets
    spine = opf.find('./{*}spine')
    ncx = manifest.get(spine.get('toc') if spine is not None else '', (None,''))[0]
    if not ncx: ncx = next((n for n,m in manifest.values() if m=='application/x-dtbncx+xml'), None)
    if ncx:
        for point in ET.fromstring(z.read(ncx)).findall('.//{*}navPoint'):
            content = point.find('./{*}content')
            if content is not None: add(ncx,content.get('src'),point.findtext('./{*}navLabel/{*}text'))
    return targets


def epub_section(book, z, name, title, events):
    if not events or not book.chapter(title): return
    for kind, value in events:
        if kind != 'image':
            book.paragraph(value, kind=='heading')
            continue
        dest = local_ref(name, value)
        if dest and PurePosixPath(dest).suffix.lower() in PICTURES:
            with z.open(dest) as f: book.picture(f, allow_upscale=False)
        else: print(f'Skipped unsupported image in {name}: {value}', file=sys.stderr)


def epub(book, path):
    with zipfile.ZipFile(path) as z:
        container = ET.fromstring(z.read('META-INF/container.xml'))
        package = container.find('.//{*}rootfile').attrib['full-path']
        opf = ET.fromstring(z.read(package))
        title = opf.findtext('./{*}metadata/{*}title')
        if title and not book.args.title: book.title = title
        manifest = {e.get('id'): (local_ref(package, e.get('href','')), e.get('media-type','')) for e in opf.findall('./{*}manifest/{*}item')}
        cover_ids = [e.get('id') for e in opf.findall('./{*}manifest/{*}item')
                     if 'cover-image' in e.get('properties', '').split()]
        cover_ids += [e.get('content') for e in opf.findall('./{*}metadata/{*}meta') if e.get('name') == 'cover']
        for cid in cover_ids:
            name, kind = manifest.get(cid, (None, ''))
            if name and kind.startswith('image/') and PurePosixPath(name).suffix.lower() in PICTURES:
                try:
                    with z.open(name) as f, Image.open(f) as cover:
                        book.cover = cover_thumbnail(ImageOps.exif_transpose(cover))
                except (OSError, KeyError):
                    continue
                book.epub_cover = True
                break
        toc = epub_toc(z,opf,package,manifest)
        for ref in opf.findall('./{*}spine/{*}itemref'):
            if ref.get('linear','yes').lower() == 'no': continue
            name, kind = manifest.get(ref.get('idref'), (None,''))
            if not name: continue
            if kind.startswith('image/') and PurePosixPath(name).suffix.lower() in PICTURES:
                if book.chapter(PurePosixPath(name).stem):
                    with z.open(name) as f: book.picture(f)
                continue
            if kind not in ('application/xhtml+xml','text/html','image/svg+xml'): continue
            links = toc.get(name, {})
            parser = XHTML(links)
            raw = z.read(name)
            encoding = 'utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'
            parser.feed(raw.decode(encoding)); parser.close(); parser.flush()
            heading = links.get('') or next((s for t,s in parser.events if t == 'heading'), PurePosixPath(name).stem)
            section = []
            for kind, value in parser.events:
                if kind == 'anchor':
                    epub_section(book,z,name,heading,section)
                    heading, section = value, []
                else: section.append((kind,value))
            epub_section(book,z,name,heading,section)


def picture_folder(book, path, names, *, unwrap_chapters=False):
    book.input_bytes = sum((path/name).stat().st_size for name in names)
    comic(book, names, lambda name: (path/name).open('rb'), path.name, unwrap_chapters=unwrap_chapters)


def import_source(book, path, image_names=None):
    book.input_bytes = 0 if path.is_dir() else path.stat().st_size
    if image_names is not None:
        picture_folder(book, path, image_names, unwrap_chapters=True)
    elif path.is_dir():
        items = [p for p in path.iterdir() if visible(p.name)]
        archives = sorted((p for p in items if p.suffix.lower() in ('.cbz','.zip')), key=lambda p:natural(p.name))
        texts = sorted((p for p in items if p.suffix.lower() == '.txt'), key=lambda p:natural(p.name))
        images = [p.relative_to(path).as_posix() for p in path.rglob('*') if p.is_file() and p.suffix.lower() in PICTURES and visible(p.relative_to(path).as_posix())]
        if sum(map(bool, (archives, texts, images))) != 1:
            raise ValueError('Use a folder of images, CBZs, or UTF-8 text files, with one type per folder.')
        book.input_bytes = sum(p.stat().st_size for p in archives+texts)
        for p in archives: cbz(book,p)
        for p in texts: text_chapters(book,p)
        if images: picture_folder(book, path, images)
    elif path.suffix.lower() in ('.cbz','.zip'): cbz(book,path)
    elif path.suffix.lower() == '.epub': epub(book,path)
    elif path.suffix.lower() == '.txt': text_chapters(book,path)
    elif path.suffix.lower() == '.pdf': pdf_nav.import_pdf(book,path,WIDTH,label_bits)
    elif path.suffix.lower() in PICTURES:
        if book.chapter(path.stem): book.picture(path)
    else: raise ValueError('Supported inputs are EPUB, TXT, CBZ, PDF, pictures, or folders.')


def chapter_range(value):
    m = re.fullmatch(r'(\d+)(?:-(\d+))?', value)
    if not m: raise argparse.ArgumentTypeError('Use N or FIRST-LAST, for example 3-7.')
    a,b = int(m[1]), int(m[2] or m[1])
    if not 1 <= a <= b: raise argparse.ArgumentTypeError('Chapter numbers start at 1.')
    return a,b


def read_exact(f, where, count):
    f.seek(where)
    raw=f.read(count)
    if len(raw)!=count: raise ValueError('Truncated Folio book.')
    return raw


def stored_strip(f, index, number, begin, end):
    start, stop=struct.unpack('<II',read_exact(f,index+number*4,8))
    if not begin<=start<stop<=end or stop-start>comp.MAX_BLOCK:
        raise ValueError('Invalid Folio strip index.')
    return read_exact(f,start,stop-start)


def repack(source, dest, profile):
    """Recompress readable packed data, preserving the original layout and book ID."""
    size=source.stat().st_size
    if size>0x7fffffff: raise ValueError('Folio file exceeds the maximum book size.')
    with source.open('rb') as f, contextlib.ExitStack() as tmp:
        magic, identity, *fields=HEADER.unpack(read_exact(f,0,HEADER.size))
        if magic not in (comp.V1,*comp.MAGICS,*comp.V3,*comp.V4): raise ValueError('Unknown Folio book format.')
        old=0 if magic==comp.V1 else magic[6]
        new=comp.PROFILES.index(profile)
        if new<old: raise ValueError('This pack already uses stronger compression. Use the original source for higher quality.')
        height,nc,np,levels,cpos,ppos,idx0,idx1=fields
        master=levels==0x102 and magic in (*comp.V3, *comp.V4)
        core_size=size
        nav=b''
        cover=b''
        if magic in (*comp.V3, *comp.V4):
            nav_size,tag=pdf_nav.FOOTER.unpack(read_exact(f,size-8,8))
            if tag!=b'FNAV' or nav_size>8000000 or nav_size>size-8: raise ValueError('Invalid PDF navigation footer.')
            core_size=size-8-nav_size
            nav=read_exact(f,core_size,nav_size)
        if magic in comp.V4:
            length, tag = pdf_nav.FOOTER.unpack(read_exact(f, core_size-8, 8))
            if tag != b'CVR4' or length > 32768 or length > core_size-8:
                raise ValueError('Invalid cover thumbnail footer.')
            core_size -= 8 + length
            cover = read_exact(f, core_size, length)
        label_size=LABEL_W*LABEL_H//8
        if not (0<height<=0x3fffffff and 0<nc<=8192 and 0<np<=500000 and (levels in (1,2) or master) and
                cpos==HEADER.size+label_size and ppos==cpos+nc*(8+label_size) and idx0==ppos+np*4 and idx0<size):
            raise ValueError('Invalid Folio book header.')
        label=read_exact(f,HEADER.size,label_size)
        chapters=read_exact(f,cpos,ppos-cpos); pages=read_exact(f,ppos,idx0-ppos)
        tagged=[n for (n,) in struct.iter_unpack('<I',pages)]
        if magic==comp.V1 and any(n&comp.WIDE for n in tagged): raise ValueError('Invalid legacy page positions.')
        starts=[n & ~comp.WIDE for n in tagged]
        if starts[0] or starts[-1]>=height or any(a>=b for a,b in zip(starts,starts[1:])):
            raise ValueError('Invalid Folio page positions.')
        previous=-1
        for i in range(nc):
            top, page=struct.unpack_from('<II',chapters,i*(8+label_size))
            if page>=np or page<=previous or top!=starts[page] or (i==0 and page!=0):
                raise ValueError('Invalid Folio chapter index.')
            previous=page
        pdf_nav.validate(nav,starts,height)
        scales=[1] if master else list(range(levels))
        counts=[((height<<i)+BAND-1)//BAND for i in scales]
        if (levels==2 and idx1!=idx0+(counts[0]+1)*4) or ((levels==1 or master) and idx1): raise ValueError('Invalid detail index.')
        indices=[idx0,idx1][:len(scales)]
        data_start=indices[-1]+4*(counts[-1]+1)
        if data_start>=size: raise ValueError('Invalid Folio index length.')
        bounds=[(struct.unpack('<I',read_exact(f,idx,4))[0],struct.unpack('<I',read_exact(f,idx+4*n,4))[0])
                for idx,n in zip(indices,counts)]
        if not 0<=bounds[0][0]-data_start<=800000 or bounds[-1][1]!=core_size or any(a>=b or b>core_size for a,b in bounds):
            raise ValueError('Invalid Folio data offset.')
        if levels==2 and bounds[0][1]!=bounds[1][0]: raise ValueError('Invalid detail data offset.')
        atlas=read_exact(f,data_start,bounds[0][0]-data_start)
        if old!=new: atlas=comp.compress_atlas(atlas,profile)
        elif atlas: comp.atlas_bytes(atlas)
        if magic==comp.V1 and not atlas:
            stops=starts[1:]+[height]
            pages=struct.pack(f'<{np}I',*(n | (comp.WIDE if end-n<WIDTH else 0) for n,end in zip(starts,stops)))
        stores=[]
        drop_fit = levels==2 and not atlas
        valid_modes = {0, 1, 2, 3}
        if magic != comp.V1:
            valid_modes.update((4, 5, 6))
        if magic in (*comp.V3, *comp.V4):
            valid_modes.update((8, 9, 10))
        if magic in comp.V4:
            valid_modes.add(11)
        for level,idx,n,(begin,end) in zip(scales,indices,counts,bounds):
            if drop_fit and level==0: continue
            store=comp.StripStorage(tmp,profile); stores.append(store)
            for i in range(n):
                block=stored_strip(f,idx,i,begin,end)
                if block[0]==comp.BAND_REFERENCE:
                    if len(block)!=5 or magic==comp.V1: raise ValueError('Invalid Folio strip reference.')
                    ref=struct.unpack_from('<I',block,1)[0]
                    if ref>=i: raise ValueError('Invalid Folio strip reference.')
                    block=stored_strip(f,idx,ref,begin,end)
                if block[0] not in valid_modes:
                    raise ValueError('Unknown Folio strip encoding.')
                if block[0]==1:
                    with Image.open(io.BytesIO(block[1:])) as im:
                        if im.size!=(WIDTH<<level,64): raise ValueError('Invalid JPEG dimensions.')
                        if magic not in (*comp.V3, *comp.V4) or old != new:
                            revised = screen_color.encode(im, profile)
                            block = min((revised, block), key=len) if profile == 'smallest' else revised
                elif block[0] in (2,8,9,10) and old!=new:
                    revised=screen_color.encode(screen_color.from_block(block,WIDTH<<level),profile)
                    block=min((block,revised),key=len)
                elif old!=new:
                    block=comp.optimize_block(block,WIDTH<<level,profile,comp.JPEG_QUALITY[profile])
                store.write(block)
            store.end()
        if drop_fit:
            fields=(*fields[:3],0x102,*fields[4:7],0)
        write_pack(dest,profile,identity,fields,label,chapters,pages,atlas,stores,nav,magic in comp.V4,cover)
    report_size(dest, profile, size)


def report_size(path, profile, source_bytes, standard_bytes=None):
    size = path.stat().st_size
    print(f'{path}\nInput {source_bytes:,} B | {profile.capitalize()} pack {size:,} B')
    comparisons = [('the input', source_bytes)]
    if standard_bytes is not None and profile != 'standard':
        comparisons.append((f'the {standard_bytes:,} B Standard Folio pack', standard_bytes))
    for name, baseline in comparisons:
        difference = abs(size - baseline)
        if difference == 0:
            print(f'Same size as {name}.')
            continue
        direction = 'smaller' if size < baseline else 'larger'
        percent = f' ({100 * difference / baseline:.1f}%)' if baseline else ''
        print(f'{difference:,} B{percent} {direction} than {name}.')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('-o','--output',type=Path,help='Output name ending in .folio.tns')
    p.add_argument('--title',help='Bookshelf title')
    resolution=p.add_mutually_exclusive_group()
    resolution.add_argument('--detail',action='store_true',help='Also pack prose at 640 pixels wide for zoom detail')
    resolution.add_argument('--fit-only',action='store_true',help='Pack sources at 320 pixels wide for minimum storage')
    p.add_argument('--gray',action='store_true',help='Store all artwork in 16 shades of grey')
    p.add_argument('--compression',choices=comp.PROFILES,default='compact',help='Storage preset (default compact)')
    p.add_argument('--quality',type=int,help='Set the colour palette budget and JPEG quality (1–95)')
    p.add_argument('--font',type=Path,default=DEFAULT_FONT,help='TTF/OTF/TTC file for prose and chapter names')
    p.add_argument('--font-index',type=int,default=0,help='Face index for a font collection')
    p.add_argument('--font-size',type=int,default=16,help='Base novel font in screen pixels (12–30, default 16)')
    p.add_argument('--chapters',type=chapter_range,default=(1,2**31-1),help='Include numbered input chapters, e.g. 3-7')
    p.add_argument('--pages-from-stdin',action='store_true',help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if not 12 <= args.font_size <= 30 or (args.quality is not None and not 1 <= args.quality <= 95): p.error('Check --font-size (12–30) and --quality (1–95).')
    image_names = None
    if args.pages_from_stdin:
        image_names = json.load(sys.stdin)
        if (not args.input.is_dir() or not isinstance(image_names,list) or not image_names or
            any(not isinstance(n,str) or Path(n).anchor or '..' in Path(n).parts or
                Path(n).suffix.lower() not in PICTURES for n in image_names)):
            p.error('A manga folder needs a list of relative image paths.')
    path = args.output or default_output(args.input, args.compression)
    if not path.name.lower().endswith('.folio.tns'): p.error('Choose an output ending in .folio.tns so the reader can find it.')
    if path.resolve() == args.input.resolve(): p.error('Choose an output separate from the source.')
    if prepared_book(args.input):
        if (args.detail or args.fit_only or args.gray or args.font!=DEFAULT_FONT or args.font_index or args.font_size!=16 or
            args.quality is not None or args.title or args.chapters!=(1,2**31-1)):
            p.error('For a prepared book use --compression and -o. Change layout or quality from the original source.')
        repack(args.input,path,args.compression)
        return
    with contextlib.ExitStack() as scratch:
        book = Book(args, scratch); import_source(book, args.input, image_names); book.finish(path)
        if book.missing_chars:
            codes = ', '.join(f'U+{ord(ch):04X}' for ch in sorted(book.missing_chars)[:8])
            print(f'The selected font lacks {len(book.missing_chars)} character(s), including {codes}. Choose a matching --font to display them.', file=sys.stderr)
        report_size(path, args.compression, book.input_bytes, book.standard_bytes)
        print(f'{len(book.chapters)} chapter(s), {len(book.pages)} reading positions')


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, KeyError, IndexError, struct.error, zlib.error, lzma.LZMAError, zipfile.BadZipFile, ET.ParseError, RuntimeError) as exc:
        sys.exit(f'Folio: {exc}')
