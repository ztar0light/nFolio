"""Encode colour strips directly in the calculator's 16-bit RGB565 space."""
import io
import struct
import zlib
from PIL import Image, ImageChops
from folio_compression import JPEG_QUALITY, inflate

def expanded(image):
    bands=image.convert('RGB').split()
    five=[(i & 248)|(i>>5) for i in range(256)]
    six=[(i & 252)|(i>>6) for i in range(256)]
    return Image.merge('RGB',tuple(b.point(lut) for b,lut in zip(bands,(five,six,five))))

def words(rgb):
    r,g,b=rgb[::3],rgb[1::3],rgb[2::3]
    return [((red&248)<<8)|((green&252)<<3)|(blue>>3) for red,green,blue in zip(r,g,b)]

def indexed(im,count):
    q=im.quantize(colors=count,method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE)
    ids=q.tobytes(); used=max(ids)+1
    palette=words(bytes(q.getpalette()[:3*used]))
    return bytes((9,used-1))+struct.pack(f'<{used}H',*palette)+zlib.compress(ids,9)


def encode_with_reference(image,profile,quality=None):
    im=expanded(image)
    r,g,b=im.split()
    low=ImageChops.add(g.point([(v&28)<<3 for v in range(256)]),b.point([v>>3 for v in range(256)]))
    high=ImageChops.add(r.point([v&248 for v in range(256)]),g.point([v>>5 for v in range(256)]))
    data=Image.merge('LA',(low,high)).tobytes()
    stride=im.width*2
    diff=int.from_bytes(data[stride:],'little')^int.from_bytes(data[:-stride],'little')
    delta=data[:stride]+diff.to_bytes(len(data)-stride,'little')
    choices=[bytes((8,0))+zlib.compress(data,9),bytes((8,1))+zlib.compress(delta,9)]
    colors=im.getcolors(256)
    if colors:
        choices.append(indexed(im,len(colors)))
    exact=min(choices,key=len)
    cap=128 if profile=='compact' else 40
    if quality is not None:
        cap=max(8,min(256,round(256*(quality/95)**2)))
    if (profile!='standard' or quality is not None) and (not colors or len(colors)>cap):
        choices.append(indexed(im,cap))
    reference=len(exact)
    doubled = False
    if im.size==(640,64):
        small=im.resize((320,32),Image.Resampling.NEAREST)
        if not ImageChops.difference(im,small.resize(im.size,Image.Resampling.NEAREST)).getbbox():
            doubled = True
            mini,mini_ref=encode_with_reference(small,profile,quality)
            choices.append(bytes((10,))+mini)
            reference=min(reference,1+mini_ref)
    if profile == 'smallest' and im.height == 64 and colors is None and not doubled:
        # Complex covers and painted panels often cost more as indexed pixels.
        # The reader's existing JPEG decoder writes directly to an RGB565 strip.
        out = io.BytesIO()
        im.save(out, 'JPEG', quality=quality or JPEG_QUALITY[profile], subsampling=2, optimize=True)
        choices.append(b'\x01' + out.getvalue())
    return min(choices,key=len),reference


def encode(image,profile,quality=None):
    return encode_with_reference(image,profile,quality)[0]


def from_block(block,width,height=64):
    if len(block)<3: raise ValueError('Incomplete native colour strip.')
    kind=block[0]; n=width*height
    if kind==10 and (width,height)==(640,64) and block[1] in (8,9):
        return from_block(block[1:],320,32).resize((width,height),Image.Resampling.NEAREST)
    if kind==8:
        data=bytearray(inflate(block[2:],n*2))
        if len(data)!=n*2 or block[1]>1:
            raise ValueError('Invalid native colour strip.')
        if block[1]:
            stride=width*2
            for i in range(stride,len(data)): data[i]^=data[i-stride]
    elif kind in (2,9):
        count=block[1]+(kind==9); offset=2+count*2
        ids=inflate(block[offset:],n)
        if not count or len(ids)!=n or max(ids)>=count:
            raise ValueError('Invalid native palette strip.')
        palette=[block[2+2*i:4+2*i] for i in range(count)]
        data=b''.join(palette[i] for i in ids)
    else:
        raise ValueError('Expected native screen colours.')
    return Image.frombytes('RGB',(width,height),bytes(data),'raw','BGR;16')
