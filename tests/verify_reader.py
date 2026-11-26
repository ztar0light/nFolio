"""Exercise the reading gestures, PDF letter reuse and per-title cover previews."""
import contextlib
import io
import random
import shutil
import struct
import tempfile
import zipfile
from pathlib import Path

from PIL import Image
import verify as v
from verify_spreads import capture, streams, recompress


def test_menu_presses(root):
    source=root/'Chapters.txt'
    source.write_text('\n\n'.join(f'# Chapter {i}\n\nPage {i}.' for i in range(1,16)),encoding='utf-8')
    device=root/'menu-presses'
    device.mkdir()
    book=device/'01.folio.tns'
    v.prepare(source,book)
    v.check_contents_menu(device)
    def row(name):
        buf=(device/(name+'.raw')).read_bytes()
        return [n for n in range(7) if struct.unpack_from('<H',buf,2*((38+25*n)*320+5))[0]==0x1b4d]
    v.run(device,'\n'.join([
        'menu','none','down 160','snap commands.raw','none','down 25','snap bounce.raw',
        'none 3','up','none','enter','none','right 150','snap chapters.raw',
        'none 3','right','none','next','none','snap next-screen.raw','left 90','snap previous-row.raw',
        'none 2','center','none 4','power',
    ]))
    assert row('commands')==row('bounce')==row('chapters')==[1]
    assert row('next-screen')==[2] and row('previous-row')==[1]
    h=v.meta(book)
    chapter_bytes=8+v.pack.LABEL_W*v.pack.LABEL_H//8
    chapter_y=struct.unpack_from('<I',book.read_bytes(),h[6]+8*chapter_bytes)[0]
    assert v.marks(device)[-1][1][0]==chapter_y
    device=root/'menu-shelf'
    device.mkdir()
    for n in range(1,13):
        shutil.copyfile(book,device/f'{n:02}.folio.tns')
    v.run(device,'\n'.join(['down 120','snap shelf.raw','right 40','snap roll.raw',
                             'none 3','right 90','snap shelf-next.raw','none','power']))
    assert row('shelf')==row('roll')==[1] and row('shelf-next')==[2]


def test_input(root):
    source = root / 'pages'
    device = root / 'reader'
    source.mkdir()
    device.mkdir()
    for i in range(4):
        Image.new('RGB', (320, 400), (30+i*40, 90, 160)).save(source / f'{i}.png')
    book = device / 'book.folio.tns'
    v.prepare(source, book)
    base = v.render(book)[0]
    v.run(device, '\n'.join([
        'touch 90 40', 'touch 180 120', 'enter', 'none', 'snap bare.raw',
        'ctrl', 'snap pointer.raw', 'ctrltouch 80 80', 'ctrltouch 100 80', 'snap moved.raw',
        'none', 'snap released.raw', 'right 75', 'snap once.raw', 'none', 'right 8', 'snap bounce.raw',
        'none 2', 'right', 'snap twice.raw', 'none', 'menu', 'none 3', 'snap menu.raw', 'menu', 'none',
        'snap closed.raw', 'power',
    ]))
    load = lambda name: (device / (name+'.raw')).read_bytes()
    assert load('bare') == load('released') == base
    assert load('pointer') != base and load('moved') != load('pointer')
    def pixel(name, x, y):
        return struct.unpack_from('<H', load(name), (y*320+x)*2)[0]
    assert pixel('pointer',160,120) == pixel('moved',170,120) == 0
    assert pixel('moved',160,120) == pixel('bare',160,120)
    assert load('once') == load('bounce') == v.render(book,400)[0]
    assert load('twice') == load('closed') == v.render(book,800)[0] != load('menu')

    v.run(device,'down 350\nnone 3\npower')
    assert v.marks(device)[-1][1][0] == 960
    v.run(device,'ctrlcenter\nnone 52\ndown 350\nnone 2\npower')
    _, state, marker = v.marks(device)[-1]
    assert state[0] > 1200 and marker.read_bytes()[35] == 1
    v.run(device,'down\nnone\npower')
    assert v.marks(device)[-1][1][0] == state[0]+6
    v.run(device,'ctrlcenter\nnone 52\npower')
    assert v.marks(device)[-1][2].read_bytes()[35] == 0
    # The legacy first-page preview is independent of the current page.
    shelf = capture(device,'esc')
    cover_pixel = struct.unpack_from('<H',shelf,(105*320+271)*2)[0]
    assert cover_pixel == struct.unpack_from('<H',base,0)[0]


def prose_pdf(path):
    pages = 14
    kids = ' '.join(f'{4+i*2} 0 R' for i in range(pages))
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               f'<< /Type /Pages /Count {pages} /Kids [{kids}] >>'.encode(),
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    rng = random.Random(1728)
    words = 'signal track river lamp mile stone east west'.split()
    for i in range(pages):
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 384] /Resources << /Font << /F1 3 0 R >> >> /Contents {5+i*2} 0 R >>'.encode())
        body = bytearray()
        for row in range(1,26):
            ink = '0 0 0.7' if row%6 == 0 else '0 0 0'
            line = ' '.join(rng.choices(words,k=8))
            body.extend(f'{ink} rg BT /F1 10 Tf 14 {384-14*row} Td ({line}) Tj ET\n'.encode())
        objects.append(b'<< /Length '+str(len(body)).encode()+b' >>\nstream\n'+body+b'endstream')
    data = bytearray(b'%PDF-1.4\n')
    offsets = []
    for n,obj in enumerate(objects,1):
        offsets.append(len(data))
        data.extend(f'{n} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref = len(data)
    data.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets:
        data.extend(f'{offset:010d} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    path.write_bytes(data)


def test_text(root):
    import pypdfium2 as pdfium
    source = root / 'prose.pdf'
    prose_pdf(source)
    small, compact = (root / (k+'.folio.tns') for k in ('small','compact'))
    v.prepare(source,small,'--compression','smallest')
    v.prepare(source,compact,'--compression','compact')
    hdr,_,blocks = streams(small)
    assert hdr[0].startswith(b'FOLIO4') and hdr[1:9] == v.meta(compact)[1:9]
    assert small.stat().st_size < compact.stat().st_size and any(m==11 for m,_,_ in blocks[0])
    again = root / 'repacked.folio.tns'
    recompress(small,again,'smallest')
    assert again.read_bytes() == small.read_bytes()

    fit = root / 'fit.folio.tns'
    v.prepare(source,fit,'--compression','smallest','--fit-only')
    _,_,blocks = streams(fit)
    # At fit width, compare the native decoder directly with PDFium. Black ink
    # is quantized to the four Smallest shades and coloured letters retain RGB565.
    with pdfium.PdfDocument(source) as doc, contextlib.closing(doc[0]) as page, contextlib.closing(page.render(scale=1)) as bmp:
        image = bmp.to_pil().convert('RGB')
    checked = coloured = 0
    for i,(mode,_,_) in enumerate(blocks[0][:6]):
        if mode != 11:
            continue
        y = i*64
        expected = bytearray()
        for r,g,b in struct.iter_unpack('BBB',image.crop((0,y,320,y+64)).tobytes()):
            if r == g == b:
                r = g = b = 85*((r//16+2)//5)
            else:
                coloured += 1
            expected.extend(struct.pack('<H',((r>>3)<<11)|((g>>2)<<5)|(b>>3)))
        assert v.render(fit,y)[0][:2*320*64] == expected
        checked += 1
    assert checked and coloured


def test_epub_cover(root):
    source = v.make_epub(root, 2)
    art = io.BytesIO()
    Image.new('RGB',(100,145),(224,16,24)).save(art,'PNG')
    for version in (2,3):
        device = root / f'cover-{version}'
        device.mkdir()
        epub = root / f'cover-{version}.epub'
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(epub,'w') as updated:
            for item in original.infolist():
                blob = original.read(item)
                if item.filename == 'OPS/book.opf':
                    if version == 2:
                        blob = blob.replace(b'</metadata>',b'<meta name="cover" content="jacket"/></metadata>')
                    flag = b' properties="cover-image"' if version == 3 else b''
                    entry = b'<item id="jacket" href="jacket.png" media-type="image/png"'+flag+b'/>'
                    blob = blob.replace(b'</manifest>',entry+b'</manifest>')
                updated.writestr(item,blob)
            updated.writestr('OPS/jacket.png',art.getvalue())
        book = device / 'novel.folio.tns'
        v.prepare(epub,book)
        legacy = root / 'no-cover.folio.tns'
        v.prepare(source,legacy)
        assert v.meta(book)[1] == v.meta(legacy)[1]
        assert v.render(book)[0] == v.render(legacy)[0]
        shelf = capture(device,'esc')
        center = struct.unpack_from('<H',shelf,(105*320+271)*2)[0]
        assert center>>11 >= 26 and (center>>5)&63 <= 6 and center&31 <= 5


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        test_menu_presses(root)
        print('PASS one move per held D-pad press in the reader menu, chapters and bookshelf, bounce filtering and explicit screen paging')
        test_input(root)
        print('PASS Ctrl-only pointer, damped dragging, held and bouncing page turns, paged limits, continuous scroll and legacy preview')
        test_text(root)
        print('PASS PDF letter reuse in 640- and 320-wide books, colour preservation, stable IDs and repeat conversion')
        test_epub_cover(root)
        print('PASS EPUB 2 and 3 declared cover outside the spine and unchanged reading positions')
