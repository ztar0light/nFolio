"""Storage encodings shared by the source converter and packed-book compressor."""
from __future__ import annotations

import hashlib
import io
import lzma
import struct
import tempfile
import zlib
from PIL import Image

PROFILES = ('standard', 'compact', 'smallest')
JPEG_QUALITY = {'standard': 82, 'compact': 74, 'smallest': 52}
LZ_FILTER = {'id': lzma.FILTER_LZMA1, 'dict_size': 262144, 'lc': 3, 'lp': 0, 'pb': 2}
WIDE = 1 << 31
LZ = 1 << 31
V1 = b'FOLIO1\r\n'
MAGICS = tuple(b'FOLIO2' + bytes([i, 10]) for i in range(len(PROFILES)))
V3 = tuple(b'FOLIO3' + bytes([i, 10]) for i in range(len(PROFILES)))
V4 = tuple(b'FOLIO4' + bytes([i, 10]) for i in range(len(PROFILES)))
BAND_REFERENCE = 7
MAX_BLOCK = 131072


def inflate(data, length):
    reader = zlib.decompressobj()
    out = reader.decompress(data, length + 1)
    if len(out) != length or not reader.eof or reader.unused_data:
        raise ValueError('Unexpected compressed block length.')
    return out


def atlas_bytes(data):
    if not data: return b''
    if len(data) < 5: raise ValueError('Incomplete font atlas.')
    tag = struct.unpack_from('<I', data)[0]
    size = tag & ~LZ
    if not 12 <= size <= 760000: raise ValueError('Invalid font atlas size.')
    if not tag & LZ: return inflate(data[4:], size)
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[LZ_FILTER])
    if len(data)<9: raise ValueError('Incomplete packed font atlas.')
    expected_sum=struct.unpack_from('<I',data,4)[0]
    raw = decoder.decompress(data[8:], max_length=size+1)
    if len(raw) != size or not decoder.eof or decoder.unused_data or zlib.adler32(raw)!=expected_sum: raise ValueError('Damaged font atlas.')
    return raw


# Map each 4-bit coverage value to its nearest level in a four-shade palette.
FOUR = tuple((i+2)//5 for i in range(16))
FOUR_NIBBLES = bytes((FOUR[b>>4]*5 << 4) | FOUR[b&15]*5 for b in range(256))
FOUR_PAIRS = bytes((FOUR[b>>4] << 2) | FOUR[b&15] for b in range(256))


def compress_atlas(data, profile):
    if not data or profile == 'standard': return data
    raw = atlas_bytes(data)
    if profile == 'smallest':
        cells = bytearray(raw)
        count = struct.unpack_from('<I', raw)[0]
        if not 1 <= count <= 16000 or 8+count*4 > len(raw): raise ValueError('Invalid glyph table.')
        offsets = struct.unpack_from(f'<{count+1}I',raw,4)
        if offsets[0] != 4+4*(count+1) or offsets[-1] != len(raw): raise ValueError('Invalid glyph offsets.')
        for start,end in zip(offsets,offsets[1:]):
            if not start+2 <= end <= len(raw): raise ValueError('Invalid glyph size.')
            cells[start+2:end] = raw[start+2:end].translate(FOUR_NIBBLES)
        raw = bytes(cells)
    fast = struct.pack('<I',len(raw)) + zlib.compress(raw,9)
    compact = struct.pack('<II',len(raw)|LZ,zlib.adler32(raw)) + lzma.compress(raw,format=lzma.FORMAT_RAW,filters=[LZ_FILTER])
    return min((data, fast, compact), key=len)


def text_planes(block):
    count = struct.unpack_from('<H',block,1)[0]
    if not 0 < count <= 8192: raise ValueError('Invalid glyph command count.')
    source = inflate(block[3:], count*5)
    planes = bytearray(count*5)
    previous_x = previous_y = 0
    for i,(glyph,x,y) in enumerate(struct.iter_unpack('<HHb',source)):
        dx = (x-previous_x) & 65535
        planes[i] = glyph & 255
        planes[count+i] = glyph >> 8
        planes[count*2+i] = dx & 255
        planes[count*3+i] = dx >> 8
        planes[count*4+i] = (y-previous_y) & 255
        previous_x, previous_y = x, y
    return b'\x04' + block[1:3] + zlib.compress(planes,9)


def optimize_block(block, width, profile, quality, original=None):
    if profile == 'standard': return block
    mode = block[0]
    if mode == 1:
        # Source conversion uses the original pixels. Repacking a previous JPEG
        # necessarily starts with the artwork available in that pack.
        if original is None:
            with Image.open(io.BytesIO(block[1:])) as stored:
                if stored.size != (width,64): raise ValueError('Incorrect JPEG strip dimensions.')
                original = stored.convert('RGB')
        out = io.BytesIO()
        original.save(out,'JPEG',quality=quality,subsampling=1 if profile=='compact' else 2,optimize=True)
        return min((block,b'\x01'+out.getvalue()),key=len)
    if mode == 3:
        return min((block,text_planes(block)),key=len)
    if mode not in (0,5): return block
    stride = width//2
    packed = inflate(block[1:],stride*64)
    if mode == 5:
        restored = bytearray(packed)
        for i in range(stride,len(restored)): restored[i] ^= restored[i-stride]
        packed = bytes(restored)
    filtered = packed[:stride] + bytes(a^b for a,b in zip(packed[stride:],packed))
    best = min((block, b'\x05'+zlib.compress(filtered,9)),key=len)
    if profile == 'smallest':
        pairs = packed.translate(FOUR_PAIRS)
        two_bit = bytes((a<<4)|b for a,b in zip(pairs[::2],pairs[1::2]))
        return min((best,b'\x06'+zlib.compress(two_bit,9)),key=len)
    return best


class StripStorage:
    def __init__(self, scratch, profile):
        self.file = scratch.enter_context(tempfile.TemporaryFile())
        self.offsets, self.seen = [], {}
        self.profile = profile

    def write(self, block):
        index = len(self.offsets)
        self.offsets.append(self.file.tell())
        if self.profile != 'standard' and len(block)>5:
            digest = hashlib.sha256(block).digest()
            first = self.seen.setdefault(digest,index)
            if first != index: block = struct.pack('<BI',BAND_REFERENCE,first)
        self.file.write(block)

    def end(self):
        self.offsets.append(self.file.tell())
