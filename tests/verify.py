#!/usr/bin/env python3
"""Regression checks for reading positions, strip rendering and the importers."""
from __future__ import annotations
import contextlib, io, os, shutil, struct, subprocess, sys, tempfile, zipfile
from pathlib import Path
from unittest import mock
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
import pack
from make_samples import NOVEL
HOST=Path(os.environ.get('FOLIO_HOST',ROOT/'build/folio-host')).resolve()

def prepare(src,out,*args):
    with contextlib.redirect_stdout(io.StringIO()): pack.main([str(src),'-o',str(out),*args])

def run(folder,events,expected=0):
    script=folder/'events'; script.write_text('none\n'+events+'\n',encoding='utf-8')
    p=subprocess.run([str(HOST),'run',str(folder/'Folio.tns'),str(script)],cwd=folder,capture_output=True,text=True)
    assert p.returncode==expected,(p.returncode,p.stderr,events)
    return p.stderr

def check_contents_menu(folder):
    run(folder,'\n'.join([
        'snap reading.raw','menu','none 3','snap reader-menu.raw','enter','none 3',
        'snap contents.raw','down','none 3','snap highlighted.raw','menu 45','none','menu 15',
        'snap parent.raw','none 3','menu','none 3','snap back-reading.raw','power',
    ]))
    def screen(name): return (folder/(name+'.raw')).read_bytes()
    assert screen('contents')!=screen('highlighted')
    assert screen('parent')==screen('reader-menu')!=screen('highlighted')
    assert screen('reading')==screen('back-reading')!=screen('parent')

def render(book,y=0,level=0,x=0,trim=0):
    target=book.parent/'capture.raw'
    p=subprocess.run([str(HOST),'render',str(book),str(y),str(level),str(x),str(target),str(trim)],capture_output=True,text=True,check=True)
    return target.read_bytes(),tuple(map(int,p.stdout.split()))

def meta(p): return pack.HEADER.unpack_from(p.read_bytes())

def marks(folder):
    records=[]
    for p in folder.glob('m*.tns'):
        b=p.read_bytes()
        if len(b)==40: records.append((int.from_bytes(b[4:8],'little'),(int.from_bytes(b[24:28],'little'),int.from_bytes(b[28:32],'little'),b[32],b[33]),p))
    return sorted(records)

def rgb(l):
    c=(l>>4)*17
    return ((c>>3)<<11)|((c>>2)<<5)|(c>>3)

def check_strips(root):
    d=root/'fixture'; d.mkdir(); a=d/'01'; b=d/'02'; a.mkdir(); b.mkdir()
    rows=[137,74,383,266]; fn=[a/'1.png',a/'2.png',a/'10.png',b/'1.png']; total=sum(rows); y=0
    for h,p in zip(rows,fn):
        data=bytes(((j+y)*7 + x//11)%256 for j in range(h) for x in range(320))
        Image.frombytes('L',(320,h),data).save(p); y+=h
    book=root/'fixture.folio.tns'; prepare(d,book,'--fit-only')
    terms=meta(book); assert terms[2:6]==(total,2,4,1)
    for y in (0,8,61,123,136,138,202,208,374,574,590,594,620):
        data,(cold,scroll)=render(book,y)
        expected=struct.pack('<76800H',*(rgb(((j+y)*7+x//11)%256) for j in range(240) for x in range(320)))
        assert data==expected, f'raw frame differs at row {y}'
        assert 1<=cold<=5 and scroll<=1,(cold,scroll)
    # A late open performs the same small number of image decodes as an early open.
    assert render(book,0)[1]==(4,0)
    archive=root/'fixture.cbz'
    with zipfile.ZipFile(archive,'w') as z:
        for path in reversed(fn): z.write(path,path.relative_to(d).as_posix())
        z.writestr('__MACOSX/fake.png',b'not art')
    duplicate=root/'duplicate.folio.tns'; prepare(archive,duplicate,'--fit-only','--title',d.name)
    assert duplicate.read_bytes()==book.read_bytes()
    limited=root/'chapter2.folio.tns'; prepare(archive,limited,'--chapters','2')
    assert meta(limited)[2:6]==(266,1,1,0x102)
    return book

def check_text(root):
    t=root/'Novel.txt'; t.write_text(NOVEL,encoding='utf-8')
    book=root/'novel.folio.tns'; raster=root/'novel-raster.folio.tns'; prepare(t,book,'--detail')
    with mock.patch.object(pack.Glyphs,'spans',lambda self,line:None): prepare(t,raster,'--detail')
    assert book.stat().st_size<raster.stat().st_size
    assert meta(book)[3]==2
    for level,x in ((0,0),(1,161),(1,320)):
        for y in (0,47,109,183,227,514,1021,2009):
            assert render(book,y,level,x)[0]==render(raster,y,level,x)[0], (y,level,x)
    return book

def check_restart(root,book,novel):
    d=root/'device'; d.mkdir(); copy=d/'A.folio.tns'; shutil.copy(book,copy)
    # Switch chapters, zoom in, pan, then cut power after the idle checkpoint.
    run(d,'menu\nnone\nenter\nnone\ndown\nnone\nenter\nnone\nzoom\nnone\nright\nnone\ndown\nnone 45\nsnap before.raw\ncut',73)
    first=marks(d)[-1]; assert first[1]==(1196,16,1,0),first
    copy.rename(d/'renamed.folio.tns')
    run(d,'snap cold.raw\nnone\ndown\npower')
    assert (d/'before.raw').read_bytes()==(d/'cold.raw').read_bytes()
    newer=marks(d)[-1]; assert newer[1]==(1204,16,1,0)
    newer[2].write_bytes(newer[2].read_bytes()[:13]); run(d,'snap recovered.raw\npower')
    assert (d/'recovered.raw').read_bytes()==(d/'before.raw').read_bytes()
    # Separate per-title bookmarks and automatic last-book launch with two books installed.
    shutil.copy(novel,d/'Z-novel.folio.tns'); run(d,'books\nnone\ndown\nnone\nenter\nnone\nnext\nnone\npower')
    run(d,'snap novel.raw\npower'); assert (d/'novel.raw').read_bytes()==render(novel,216)[0]
    run(d,'books\nnone\nup\nnone\nenter\nnone\nsnap first.raw\npower'); assert (d/'first.raw').read_bytes()==(d/'before.raw').read_bytes()
    # A held direction triggers a checkpoint even without an idle period or clean exit.
    hold=root/'held'; hold.mkdir(); shutil.copy(novel,hold/'book.folio.tns')
    run(hold,'ctrlcenter\nnone\ndown 255\ncut',73)
    held=marks(hold)[-1]; assert 210<=held[1][0]<=250 and held[2].read_bytes()[35]==1,held
    # On-key exit commits the current (unsettled) position too.
    saved=marks(hold)[-1][1][0]; run(hold,'down\npower'); assert marks(hold)[-1][1][0]==saved+6
    missing=root/'empty'; missing.mkdir(); assert 'beside' in run(missing,'esc',1)

def make_epub(root,version):
    file=root/f'two{version}.epub'; img=io.BytesIO(); Image.new('RGB',(40,50),(27,115,208)).save(img,'PNG')
    ncx='<ncx><navMap>'+''.join(f'<navPoint><navLabel><text>Part {x}</text></navLabel><content src="ch.xhtml#{x}"/></navPoint>' for x in ('one','two'))+'</navMap></ncx>'
    nav='<html xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>'+''.join(f'<li><a href="ch.xhtml#{x}">Part {x}</a></li>' for x in ('one','two'))+'</ol></nav></body></html>'
    with zipfile.ZipFile(file,'w') as z:
        z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        item='<item id="nav" properties="nav" href="nav.xhtml" media-type="application/xhtml+xml"/>' if version==3 else '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        z.writestr('OPS/book.opf','<package><metadata><title>Signals</title></metadata><manifest>'+item+'<item id="a" href="ch.xhtml" media-type="application/xhtml+xml"/></manifest><spine toc="ncx"><itemref idref="a"/></spine></package>')
        z.writestr('OPS/ch.xhtml','<html><head><title>Ignore me</title></head><body><h1 id="one">First</h1><p>Hello <em>reader</em>.</p><img src="art%20one.png"/><h1 id="two">Second</h1><p>At the next stop.</p></body></html>')
        z.writestr('OPS/art one.png',img.getvalue()); z.writestr('OPS/nav.xhtml',nav); z.writestr('OPS/toc.ncx',ncx)
    return file

def check_inputs(root):
    outputs=[]
    for version in (2,3):
        f=root/f'epub{version}.folio.tns'; prepare(make_epub(root,version),f); h=meta(f); assert h[3]==2
        assert h[4]>=3 and render(f)[0]; outputs.append(f.read_bytes())
    assert outputs[0]==outputs[1], 'EPUB 2 NCX and EPUB 3 nav must resolve the same anchors'
    try: import pypdfium2
    except ImportError: print('PDF check skipped (optional pypdfium2 missing)'); return
    imgs=[Image.new('RGB',(640,700),c) for c in ('#137776','#b05287')]; path=root/'pages.pdf'
    imgs[0].save(path,save_all=True,append_images=imgs[1:]); pdf=root/'pdf.folio.tns'; prepare(path,pdf,'--detail')
    assert meta(pdf)[2:6]==(700,1,2,0x102); assert render(pdf,330)[0]

def check_deep_jump(root):
    d=root/'tall'; d.mkdir()
    im=Image.new('L',(320,1000),'white'); im.paste(32,(148,0,151,1000)); im.save(d/'000.png')
    for i in range(1,100): shutil.copyfile(d/'000.png',d/f'{i:03}.png')
    path=root/'tall.folio.tns'; prepare(d,path,'--fit-only')
    assert meta(path)[2] == 100000
    assert render(path,95007)[1] == (5,0)

def check_corrupt(root,book):
    d=root/'bad'; d.mkdir(); path=d/'broken.folio.tns'; raw=book.read_bytes()
    path.write_bytes(raw[:-53]); assert 'Incomplete' in run(d,'esc',1)
    data=bytearray(raw); h=pack.HEADER.unpack_from(data); first=struct.unpack_from('<I',data,h[-2])[0]; data[first]=153
    path.write_bytes(data); assert 'strip' in run(d,'esc',1); assert not marks(d)

def check_desktop_entry(root):
    # Start a fresh interpreter like Pack.cmd. Importing pack in this test
    # process would hide stdlib shadowing when zipfile or lzma is preloaded.
    folder = root / 'Desktop batch'
    folder.mkdir()
    novel = folder / 'Sample novel.txt'
    novel.write_text(NOVEL, encoding='utf-8')
    comic = folder / 'Sample manga.cbz'
    page = io.BytesIO()
    Image.new('RGB', (800, 1200), (36, 94, 101)).save(page, 'PNG')
    with zipfile.ZipFile(comic, 'w') as archive:
        archive.writestr('001.png', page.getvalue())
    for source, layout in ((novel, 1), (comic, 0x102)):
        result = subprocess.run([sys.executable, str(ROOT / 'pack.py'), str(source),
                                 '--compression', 'smallest'], cwd=folder, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        book = source.with_suffix('.folio.tns')
        header = meta(book)
        assert header[0] == pack.comp.V3[pack.comp.PROFILES.index('smallest')] and header[5] == layout
        assert len(render(book)[0]) == 320 * 240 * 2

def check_size_report(root):
    output = root / 'size-report.folio.tns'
    with output.open('wb') as stream:
        stream.truncate(5_327_952)
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        pack.report_size(output, 'smallest', 3_016_582, 9_800_057)
    assert '2,311,370 B (76.6%) larger than the input.' in log.getvalue()
    assert '4,472,105 B (45.6%) smaller than the 9,800,057 B Standard Folio pack.' in log.getvalue()
    for original, message in ((5_327_952, 'Same size as the input.'),
                              (10_655_904, '5,327,952 B (50.0%) smaller than the input.'),
                              (0, '5,327,952 B larger than the input.')):
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            pack.report_size(output, 'compact', original)
        assert log.getvalue().splitlines()[-1] == message
        assert 'Standard' not in log.getvalue()

if __name__=='__main__':
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp); book=check_strips(root); print('PASS pixel-exact strips, natural order, CBZ, chapter subsets, cached reads')
        novel=check_text(root); print('PASS shared font rendering against raster reference at both zoom levels')
        check_restart(root,book,novel); print('PASS cold resume, rename, torn write, two-book shelf, held-key and power checkpoints')
        check_inputs(root); print('PASS EPUB 2/3 anchor chapters and inline art, optional PDF import')
        check_desktop_entry(root); print('PASS desktop Smallest + Auto from a fresh interpreter for prose and colour manga')
        check_size_report(root); print('PASS separate input and Standard Folio size comparisons')
        check_corrupt(root,book); print('PASS truncated pack and invalid image blocks')
        check_deep_jump(root); print('PASS 100,000 row book: 5 decoded strips for a deep jump, 0 more for next scroll')
