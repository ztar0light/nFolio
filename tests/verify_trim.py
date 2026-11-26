"""Check the handheld's fixed-centre trim against source pixels and saved books."""
from fractions import Fraction
from pathlib import Path
import shutil
import struct
import tempfile

from PIL import Image
import verify as v
from verify_navigation import navigation_pdf
from verify_spreads import frame, leaf_source
import screen_color


def presses(key, count):
    return (key+'\nnone 3\n')*count


def screen(folder, name):
    return (folder/(name+'.raw')).read_bytes()


def test_pixels(root):
    for density in (1, 2):
        width=320*density
        image=Image.frombytes('RGB',(width,480*density),
            bytes(c for y in range(480*density) for x in range(width)
                  for c in ((x*3+y)&255,(x+y*5)&255,(x*7+y*3)&255)))
        source=root/f'grid-{density}.png'
        image.save(source)
        book=root/f'grid-{density}.folio.tns'
        v.prepare(source,book,'--compression','standard',*(['--fit-only'] if density==1 else []))
        words=screen_color.words(image.tobytes())

        def sample(pixel, middle, scale):
            pos=middle*density+(Fraction(2*pixel+1,2)-middle)*density/scale-Fraction(1,2)
            pos=max(0,pos)
            index=int(pos)
            return index,int((pos-index)*32+Fraction(1,2))

        def blend(a, b, weight):
            result=0
            for shift,mask in ((11,31),(5,63),(0,31)):
                left=(a>>shift)&mask
                right=(b>>shift)&mask
                result|=((left*(32-weight)+right*weight)//32)<<shift
            return result

        for step in range(1,21):
            scale=Fraction(20+step,20)
            top=67
            pixels,reads=v.render(book,top,trim=step)
            for x in (0,1,57,159,160,238,318,319):
                sx,wx=sample(x,160,scale)
                for y in (0,15,31,63,64,119,120,201,238,239):
                    sy,wy=sample(y,0,scale)
                    i=(top*density+sy)*width+sx
                    expected=blend(blend(words[i],words[i+1],wx),
                                   blend(words[i+width],words[i+width+1],wx),wy)
                    assert struct.unpack_from('<H',pixels,2*(320*y+x))[0]==expected,(density,step,x,y)
            assert reads[0]<=9 and reads[1]<=1,(density,step,reads)


def test_reading(root):
    source,art=leaf_source(root)
    folder=root/'shelf'
    folder.mkdir()
    book=folder/'A.folio.tns'
    v.prepare(source,book)
    v.run(folder,'divide\nnone 55\nsnap fit.raw\nmultiply 80\nnone\nmultiply 20\nnone 55\nsnap first.raw\n'
          +presses('multiply',3)+'none 55\nsnap twenty.raw\nright\nnone 3\nsnap next.raw\nleft\nnone 3\n'
          +'zoom\nnone 3\nsnap detail.raw\nzoom\nnone 3\nsnap back.raw\nnext\nnone 3\nsnap scroll.raw\npower')
    assert screen(folder,'fit')==v.render(book)[0]
    assert screen(folder,'first')==v.render(book,6,trim=1)[0]
    assert screen(folder,'twenty')==screen(folder,'back')==v.render(book,20,trim=4)[0]
    assert screen(folder,'detail')==v.render(book,80,1,106)[0]
    assert screen(folder,'next')==v.render(book,500,trim=4)[0]
    assert screen(folder,'scroll')==v.render(book,200,trim=4)[0]
    assert v.marks(folder)[-1][2].read_bytes()[35]==8
    # A second title gets its own trim, including when the first is renamed.
    novel=folder/'B.folio.tns'
    shutil.copyfile(v.ROOT/'folio'/'Light Novels'/'PlatformSix.folio.tns',novel)
    v.run(folder,'snap cold.raw\nbooks\nnone 3\ndown\nnone 3\nenter\nnone 3\nsnap fresh.raw\n'
          +presses('multiply',2)+'none 55\nsnap b.raw\nbooks\nnone 3\nup\nnone 3\nenter\nnone 3\nsnap a.raw\npower')
    assert screen(folder,'cold')==screen(folder,'a')==screen(folder,'scroll')
    assert screen(folder,'fresh')==v.render(novel)[0]
    assert screen(folder,'b')==v.render(novel,11,trim=2)[0]
    book=book.rename(folder/'A moved.folio.tns')
    v.run(folder,'snap renamed.raw\nbooks\nnone 3\ndown\nnone 3\nenter\nnone 3\nsnap b-again.raw\npower')
    assert screen(folder,'renamed')==screen(folder,'a') and screen(folder,'b-again')==screen(folder,'b')
    # Both trim keys leave the fitted page pair and its centre fold intact.
    v.run(folder,'books\nnone 3\nup\nnone 3\nenter\nnone 3\nspread\nnone 3\n'+presses('multiply',24)
          +'none 55\nsnap full.raw\n'+presses('divide',24)+'none 55\nsnap whole.raw\npower')
    assert screen(folder,'full')==screen(folder,'whole')==frame(art)


def test_scrolling(root):
    folder=root/'scroll'
    folder.mkdir()
    book=folder/'rails.folio.tns'
    shutil.copyfile(v.ROOT/'folio'/'Manga'/'TwinRails.folio.tns',book)
    v.run(folder,presses('multiply',4)+'down 500\nnone 55\nsnap boundary.raw\nctrlcenter\nnone 55\n'
          +'next\nnone 3\nnext\nnone 3\nsnap continuous.raw\nzoom\nnone 3\nmultiply\nnone 55\nsnap adjusted.raw\npower')
    assert screen(folder,'boundary')==v.render(book,280,trim=4)[0]
    assert screen(folder,'continuous')==v.render(book,640,trim=4)[0]
    assert screen(folder,'adjusted')==v.render(book,644,trim=5)[0]
    marker=v.marks(folder)[-1][2].read_bytes()
    assert marker[32]==0 and marker[35]==(5<<1|1)
    v.run(folder,'snap cold.raw\ndown 500\nnone 3\nsnap last.raw\npower')
    assert screen(folder,'cold')==screen(folder,'adjusted')
    assert screen(folder,'last')==v.render(book,768,trim=5)[0]


def test_links(root):
    pdf=root/'links.pdf'
    navigation_pdf(pdf)
    for spread,x,y in ((False,20,120),(True,198,60)):
        folder=root/('spread-links' if spread else 'page-links')
        folder.mkdir()
        book=folder/'linked.folio.tns'
        v.prepare(pdf,book)
        # Moving the pointer uses half the physical trackpad displacement.
        start_x,end_x=(300,300+2*(x-160)) if x<160 else (10,10+2*(x-160))
        v.run(folder,presses('multiply',4)+('spread\nnone 3\n' if spread else '')+'none 55\n'
              +f'ctrltouch {start_x} 220\nctrltouch {end_x} {220+2*(y-120)}\n'
              +f'ctrlclick {end_x} {220+2*(y-120)}\nnone 3\nsnap linked.raw\npower')
        assert screen(folder,'linked')==v.render(book,645,trim=4)[0],spread


def test_journal(root):
    folder=root/'journal'
    folder.mkdir()
    book=folder/'book.folio.tns'
    shutil.copyfile(v.ROOT/'folio'/'Manga'/'TwinRails.folio.tns',book)
    v.run(folder,'down\nnone 3\npower')
    old=v.marks(folder)[-1][2].read_bytes()
    assert old[:4]==b'FJS1'
    v.run(folder,'snap legacy.raw\nmultiply\nnone 55\nsnap trimmed.raw\ncut',73)
    assert screen(folder,'legacy')==v.render(book,6)[0]
    assert screen(folder,'trimmed')==v.render(book,12,trim=1)[0]
    latest=v.marks(folder)[-1][2]
    assert latest.read_bytes()[:4]==b'FJS2'
    v.run(folder,'snap recovered.raw\npower')
    assert screen(folder,'recovered')==screen(folder,'trimmed')
    latest.write_bytes(latest.read_bytes()[:19])
    v.run(folder,'snap fallback.raw\npower')
    assert screen(folder,'fallback')==screen(folder,'legacy')


if __name__=='__main__':
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        test_pixels(root)
        print('PASS all 20 trim steps against source RGB565 pixels at both source resolutions and cached scrolling')
        test_reading(root)
        test_scrolling(root)
        print('PASS per-press trim, limits, untrimmed spreads, page turns, detail toggle and per-book cold resume')
        test_links(root)
        test_journal(root)
        print('PASS cropped PDF links in paged and spread views, old saves and interrupted checkpoints')
