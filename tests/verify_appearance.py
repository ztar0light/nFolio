"""Regression checks for facing-page detail, menu artwork and theme persistence."""
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

from PIL import Image
import verify as v
from verify_navigation import navigation_pdf
from verify_spreads import frame, leaf_source, solid_halves


def press(*keys):
    return ''.join(key+'\nnone 3\n' for key in keys)


def screen(folder, name):
    return (folder/(name+'.raw')).read_bytes()


def pixel(pixels, x, y):
    return struct.unpack_from('<H', pixels, 2*(320*y+x))[0]


def mean(a, b):
    return (a & b) + (((a ^ b) & 0xf7de) >> 1)


def zoomed_fold(art, pan_x=0, pan_y=0):
    # Sample the original, uncut artwork. Neither the expected scale nor
    # the centre fold comes from the handheld's per-leaf geometry.
    colors = [v.rgb(g) for g in art.tobytes()]
    def footprint(offset, pixel):
        return tuple((4*(pixel+offset)+phase)//6 for phase in (1,3))
    columns = [footprint(80+pan_x, x) for x in range(320)]
    output = []
    for y in range(240):
        y0,y1 = footprint(60+pan_y, y)
        for x0,x1 in columns:
            top = mean(colors[y0*320+x0], colors[y0*320+x1])
            bottom = mean(colors[y1*320+x0], colors[y1*320+x1])
            output.append(mean(top, bottom))
    return struct.pack('<76800H', *output)


def test_spread(root):
    source, art = leaf_source(root)
    folder = root/'facing'
    folder.mkdir()
    book = folder/'A.folio.tns'
    v.prepare(source, book)
    v.run(folder, press(*['multiply']*4)+'none 55\nsnap trim.raw\n'
          +press('spread','divide','multiply')+'snap fit.raw\ncenter\nnone 27\nsnap zoom.raw\n'
          +press('right','down')+'snap panned.raw\nnone 45\ncut', 73)
    trimmed = v.render(book, 20, trim=4)[0]
    assert screen(folder,'trim')==trimmed
    assert screen(folder,'fit')==frame(art)
    assert screen(folder,'zoom')==zoomed_fold(art)
    panned = zoomed_fold(art,12,12)
    assert screen(folder,'panned')==panned
    saved = v.marks(folder)[-1][2].read_bytes()
    assert saved[:4]==b'FJS3' and (saved[29],saved[30],saved[35])==(92,72,8)
    v.run(folder, 'snap cold.raw\n'+press(*['right','down']*8)+'snap edge.raw\n'
          +press('plus')+'snap other-pair.raw\n'+press('minus')+'snap recentered.raw\n'
          +'center\nnone 3\ncenter\nnone 26\nsnap restored.raw\n'
          +'center\nnone 3\ncenter\nnone 26\nsnap entered.raw\npower')
    assert screen(folder,'cold')==panned
    assert screen(folder,'edge')==zoomed_fold(art,80,60)
    assert screen(folder,'other-pair')==solid_halves(170,51)
    assert screen(folder,'recentered')==screen(folder,'zoom')
    assert screen(folder,'restored')==trimmed and screen(folder,'entered')==frame(art)


def test_spread_link(root):
    folder = root/'spread-link'
    folder.mkdir()
    source = root/'link.pdf'
    navigation_pdf(source)
    book = folder/'link.folio.tns'
    v.prepare(source, book)
    # Screen (190,25) is above the link at fit. In the panned, magnified
    # right-hand leaf it lands inside the first PDF page's link.
    v.run(folder, press(*['multiply']*4,'spread','zoom','right','down')
          +'ctrltouch 10 220\nctrltouch 70 30\nctrlclick 70 30\nnone 55\nsnap linked.raw\npower')
    assert screen(folder,'linked')==v.render(book,645,trim=4)[0]


def test_wide_detail(root):
    # A prejoined 2:1 scan fills the screen height at 1.5x. One-pixel
    # stripes in its 640-wide master disappear from a 320-wide fit image.
    folder = root/'wide'
    folder.mkdir()
    source = root/'panorama.png'
    scan = bytes(255*((x+y)&1) for y in range(320) for x in range(640))
    Image.frombytes('L',(640,320),scan).save(source)
    v.prepare(source,folder/'panorama.folio.tns','--compression','standard')
    v.run(folder,press('spread','zoom')+'snap wide.raw\n'+press('zoom','zoom')+'snap again.raw\npower')
    def samples(i):
        return ((4*i+1)//3,(4*i+3)//3)
    native = [v.rgb(g) for g in scan]
    expected = []
    for y in range(240):
        y0,y1 = samples(y)
        for x in range(320):
            x0,x1 = samples(x+80)
            a = mean(native[y0*640+x0],native[y0*640+x1])
            b = mean(native[y1*640+x0],native[y1*640+x1])
            expected.append(mean(a,b))
    assert len(set(expected))>1
    assert screen(folder,'wide')==screen(folder,'again')==struct.pack('<76800H',*expected)


def test_themes(root):
    folder = root/'two books'
    folder.mkdir()
    a = folder/'A.folio.tns'
    b = folder/'B.folio.tns'
    shutil.copyfile(v.ROOT/'folio/PDF/StationAtlas.folio.tns', a)
    shutil.copyfile(v.ROOT/'folio/Manga/TwinRails.folio.tns', b)
    # This reserved book doubles as a fallback wallpaper. It cannot add a shelf row.
    shutil.copyfile(b, folder/'background.folio.tns')
    source = folder/'evening art.v2.png'
    im = Image.new('RGB',(320,240),'red')
    im.paste('blue',(160,0,320,240))
    im.save(source)
    converter = str(v.ROOT/'wallpaper.py')
    subprocess.run([sys.executable,converter,str(source)],check=True,capture_output=True)
    custom = folder/'background.tns'
    raw = custom.read_bytes()
    assert raw[:8]==struct.pack('<4sHH',b'FWP1',320,240) and len(raw)==153608
    assert (pixel(raw[8:],10,50),pixel(raw[8:],300,50))==(0xf800,0x001f)
    original = source.read_bytes()
    assert subprocess.run([sys.executable,converter,str(source),'-o',str(source)],capture_output=True).returncode
    assert source.read_bytes()==original

    def background(name):
        pixels = screen(folder,name)
        return (pixel(pixels,16,206),pixel(pixels,302,206))

    v.run(folder, 'snap plain.raw\n'+press('menu','enter','enter','enter','esc')+'snap custom.raw\n'
          +press('menu','down','enter','esc')+'snap dark.raw\npower')
    def tinted(paper):
        return tuple(mean(paper,mean(paper,color)) for color in (0xf800,0x001f))
    assert background('plain')==(0xf7bc,0xf7bc)
    assert background('custom')==tinted(0xf7bc) and background('dark')==tinted(0x10c3)
    v.run(folder,'snap cold.raw\n'+press('down')+'snap last-row.raw\n'+press('down')+'snap limit.raw\n'
          +press('up','enter')+'snap original.raw\n'+press('menu')+'snap reader.raw\n'
          +press('enter')+'snap contents.raw\n'+press('menu','down','down','enter')+'snap style.raw\n'
          +press('down','enter','esc','menu')+'snap inverted.raw\npower')
    assert screen(folder,'cold')==screen(folder,'dark')
    assert screen(folder,'last-row')==screen(folder,'limit')
    assert all(background(name)==tinted(0x10c3) for name in ('reader','contents','style'))
    normal = v.render(a)[0]
    assert screen(folder,'original')==normal
    assert screen(folder,'inverted')==bytes(value^255 for value in normal)
    v.run(folder,'snap cold-page.raw\npower')
    assert screen(folder,'cold-page')==screen(folder,'inverted')
    # Truncate the newest global setting. The earlier dark-menus choice survives.
    slots = sorted(folder.glob('folio-style-*.tns'),key=lambda p:struct.unpack_from('<I',p.read_bytes(),4)[0])
    assert len(slots)==2
    slots[-1].write_bytes(slots[-1].read_bytes()[:7])
    v.run(folder,'snap recovered.raw\npower')
    assert screen(folder,'recovered')==normal
    custom.unlink()
    v.run(folder,press('books')+'snap packed-wall.raw\npower')
    custom.write_bytes(b'FWP1'+bytes(4))
    v.run(folder,press('books')+'snap fallback-wall.raw\npower')
    assert screen(folder,'fallback-wall')==screen(folder,'packed-wall')
    (folder/'background.folio.tns').rename(folder/'hidden.old')
    v.run(folder,press('books')+'snap missing.raw\npower')
    assert background('packed-wall')!=background('missing')==(0x10c3,0x10c3)


def test_selected(root):
    folder = root/'selected'
    folder.mkdir()
    for name,color in [('A','red'),('B','blue')]:
        source = root/(name+'.png')
        Image.new('RGB',(320,480),color).save(source)
        v.prepare(source,folder/(name+'.folio.tns'),'--title',name)
    v.run(folder,press('menu','enter','esc')+'snap no-last.raw\n'+press('enter','books')
          +'snap last-a.raw\n'+press('down')+'snap last-b.raw\n'
          +press('menu','enter','esc')+'snap selected-b.raw\n'
          +press('up')+'snap selected-a.raw\n'+press('down','enter','menu')+'snap reading-b.raw\npower')
    def backdrop(name):
        return pixel(screen(folder,name),155,206)
    red = mean(0xf7bc,mean(0xf7bc,0xf800))
    blue = mean(0xf7bc,mean(0xf7bc,0x001f))
    assert backdrop('no-last')==0xf7bc
    assert backdrop('last-a')==backdrop('last-b')==backdrop('selected-a')==red
    assert backdrop('selected-b')==backdrop('reading-b')==blue


if __name__=='__main__':
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        test_spread(root)
        test_spread_link(root)
        test_wide_detail(root)
        print('PASS fitted and 1.5x spreads, fold pixels, pan bounds, turn, centre gestures, saved trim, cold resume and panned PDF hit')
        test_themes(root)
        test_selected(root)
        print('PASS menu artwork on all screens, selected/last/custom sources, hidden files, independent dark themes and torn settings')
