"""Exercise the touchpad, multi-book sections, PDF navigation and 640-wide screen-colour packs."""
from pathlib import Path
import shutil
import struct
import tempfile
from PIL import Image, ImageDraw
import verify as v
from verify_spreads import streams, capture, recompress
import pdf_nav
import screen_color

def navigation_pdf(path):
    # All artwork and links are original. Page 3 also has a crop and rotation.
    objs={
        1:b'<< /Type /Catalog /Pages 2 0 R /Outlines 9 0 R >>',
        2:b'<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>',
        3:b'<< /Type /Page /Resources << /Font << /F1 18 0 R >> >> /Parent 2 0 R /MediaBox [0 0 320 480] /Contents 6 0 R /Annots [14 0 R 15 0 R 16 0 R] >>',
        4:b'<< /Type /Page /Resources << /Font << /F1 18 0 R >> >> /Parent 2 0 R /MediaBox [0 0 320 480] /Contents 7 0 R >>',
        5:b'<< /Type /Page /Resources << /Font << /F1 18 0 R >> >> /Parent 2 0 R /MediaBox [0 0 480 360] /CropBox [30 20 450 340] /Rotate 90 /Contents 8 0 R /Annots [17 0 R] >>',
        18:b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        9:b'<< /Type /Outlines /First 10 0 R /Last 13 0 R /Count 4 >>',
        10:b'<< /Title (Contents) /Parent 9 0 R /Dest [3 0 R /Fit] /Next 11 0 R >>',
        11:b'<< /Title (The footbridge) /Parent 9 0 R /Dest [4 0 R /Fit] /Prev 10 0 R /Next 13 0 R /First 12 0 R /Last 12 0 R /Count 1 >>',
        12:b'<< /Title (The survey mark) /Parent 11 0 R /A << /S /GoTo /D [4 0 R /XYZ 30 315 null] >> >>',
        13:b'<< /Title (Platform plan) /Parent 9 0 R /Dest [5 0 R /Fit] /Prev 11 0 R >>',
        14:b'<< /Type /Annot /Subtype /Link /Rect [40 330 270 390] /Dest [4 0 R /XYZ 30 315 null] >>',
        15:b'<< /Type /Annot /Subtype /Link /Rect [40 200 270 260] /A << /S /GoTo /D [5 0 R /Fit] >> >>',
        16:b'<< /Type /Annot /Subtype /Link /Rect [40 80 270 120] /A << /S /URI /URI (https://example.org/) >> >>',
        17:b'<< /Type /Annot /Subtype /Link /Rect [130 110 190 170] /Dest [3 0 R /Fit] >>',
    }
    def text(size,x,y,phrase):
        return f'BT /F1 {size} Tf {x} {y} Td ({phrase}) Tj ET '.encode()
    paper=b'0.96 0.94 0.88 rg 0 0 480 480 re f '
    navy=b'0.10 0.18 0.24 rg '; teal=b'0.13 0.37 0.38 rg '; white=b'1 1 1 rg '
    index=(paper+navy+text(30,34,423,'Station Atlas')+text(12,36,401,'CONTENTS')+
           teal+b'40 330 230 60 re f 40 200 230 60 re f '+white+
           text(20,54,364,'The survey mark')+text(11,54,344,'Jump inside The footbridge')+
           text(20,54,234,'Platform plan')+text(11,54,214,'Open the rotated map')+
           navy+text(11,40,303,'Hold Ctrl, point at a box and click.')+
           text(12,40,138,'Menu also opens the PDF bookmarks.'))
    bridge=(paper+navy+text(25,24,430,'The footbridge')+text(13,26,401,'Field notes from the north bank.')+
            teal+b'0 306 320 8 re f '+navy+text(23,24,280,'The survey mark')+
            text(12,24,254,'The bookmark lands at the teal line.')+
            b'2 w 24 145 m 76 217 l 128 145 l 180 217 l 232 145 l 284 217 l S 24 143 270 6 re f '+
            text(11,24,111,'Riveted steel trusses over the river.'))
    plan=(paper+b'q 0 1 -1 0 450 20 cm '+navy+text(25,27,376,'Platform plan')+
          teal+b'30 220 250 8 re f 47 197 165 8 re f '+navy+
          text(12,30,170,'A return link is marked on the map.')+b'Q '+
          b'0.17 0.44 0.43 rg 130 110 60 60 re f '+white+
          b'q 0 1 -1 0 185 113 cm '+text(11,5,24,'Contents')+b'Q ')
    paints=[index,bridge,plan]
    for i,paint in enumerate(paints,6):
        objs[i]=b'<< /Length '+str(len(paint)).encode()+b' >>\nstream\n'+paint+b'\nendstream'
    out=bytearray(b'%PDF-1.4\n'); offsets=[]
    for i in range(1,len(objs)+1):
        offsets.append(len(out));out.extend(f'{i} 0 obj\n'.encode()+objs[i]+b'\nendobj\n')
    at=len(out);out.extend(f'xref\n0 {1+len(objs)}\n0000000000 65535 f \n'.encode())
    for offset in offsets: out.extend(f'{offset:010d} 00000 n \n'.encode())
    out.extend(f'trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{at}\n%%EOF\n'.encode())
    path.write_bytes(out)

def navigation(p):
    raw=p.read_bytes(); size,tag=pdf_nav.FOOTER.unpack_from(raw,len(raw)-8);assert tag==b'FNAV'
    if not size:return [],[]
    record=raw[-8-size:-8];tag,nm,nl=pdf_nav.HEADER.unpack_from(record)
    end=12+nm*(pdf_nav.MARK.size+660)
    return [pdf_nav.MARK.unpack_from(record,12+i*(pdf_nav.MARK.size+660)) for i in range(nm)],list(pdf_nav.LINK.iter_unpack(record[end:]))

def test_pdf(root):
    path=root/'linked.pdf';navigation_pdf(path);dev=root/'pdf-reader';dev.mkdir()
    book=dev/'book.folio.tns';v.prepare(path,book)
    v.check_contents_menu(dev)
    marks,links=navigation(book)
    assert marks==[(0,0,0),(480,0,0),(645,30,1),(960,0,0)],marks
    assert links[0][:7]==(0,90,150,40,270,645,30) and len(links)==3,links
    assert links[2][:7]==(2,1060,1120,90,150,0,0),links
    # The centre-positioned pointer is over the first link as soon as Ctrl is held.
    assert capture(dev,'ctrlcenter\nnone')==v.render(book,645)[0]
    assert capture(dev,'menu\nnone\ncenter\nnone\ndown\nnone 2\ndown\nnone\ncenter')==v.render(book,645)[0]
    # Click the first bookmark with a moved pointer in the list.
    out=capture(dev,'menu\nnone\nenter\nnone\nctrltouch 160 200\nctrltouch 160 48\nctrlclick 160 48\nnone')
    assert out==v.render(book,0)[0]
    # The same link hits from the 1.5x view after a horizontal pan.
    assert capture(dev,'zoom\nnone\nright\nnone\nctrltouch 100 100\nctrltouch 100 196\nctrlclick 100 196\nnone')==v.render(book,1290,1,60)[0]
    part=root/'part.folio.tns'; v.prepare(path,part,'--chapters','2')
    marks2,links2=navigation(part);assert marks2==[(0,0,0),(165,30,1)] and not links2
    tighter=root/'small.folio.tns';recompress(book,tighter,'smallest');assert navigation(tighter)==(marks,links)
    return book

def test_gestures(root):
    source=root/'scans';source.mkdir()
    for n in range(4):
        im=Image.new('RGB',(640,800),(23+n*33,81,155))
        d=ImageDraw.Draw(im);d.rectangle((60,270,340,405),fill=(226,100,40));im.save(source/f'{n}.png')
    dev=root/'gestures';dev.mkdir();book=dev/'A.folio.tns';v.prepare(source,book)
    assert v.meta(book)[5]==0x102
    assert capture(dev,'center 6\nnone 24')==v.render(book,0,1)[0]
    ref=capture(dev,'spread')
    assert capture(dev,'center\nnone 3\ncenter\nnone 20')==v.render(book,0,1)[0]
    assert capture(dev,'center\nnone 3\ncenter\nnone 20')==ref
    # A long single click magnifies the same two pages. Its next single click fits them again.
    detailed=capture(dev,'center 42\nnone 24')
    assert detailed not in (ref,v.render(book,0,1)[0])
    assert capture(dev,'center\nnone 24')==ref
    v.run(dev,'menu\nnone\ndown\nnone\ncenter\nnone\nesc')
    assert capture(dev,'none')==ref
    return book

def test_sections(root,one,two):
    shelf=root/'folio';manga=shelf/'Manga';nested=manga/'Volume 1';ln=shelf/'Light Novels'
    nested.mkdir(parents=True);ln.mkdir()
    a=nested/'Same.folio.tns';b=ln/'Same.folio.tns'
    shutil.copy(one,a);shutil.copy(two,b)
    # Alphabetical sections precede individual titles. Start in Light Novels.
    v.run(shelf,'enter\nnone\nenter\nnone\nnext\nnone\ndown\nnone\nsnap b.raw\nesc\nnone\nesc\nnone\ndown\nnone\ncenter\nnone 3\ncenter\nnone 3\ncenter\nnone\ndown\nnone\nzoom\nnone\nsnap a.raw\npower')
    assert (shelf/'a.raw').read_bytes()!= (shelf/'b.raw').read_bytes()
    # Rehome a title, restart, and switch to the other saved title without reopening the app.
    renamed=shelf/'Moved.folio.tns';a.rename(renamed)
    v.run(shelf,'snap again-a.raw\nesc\nnone\nup\nnone 2\nup\nnone\nenter\nnone\nenter\nnone\nsnap again-b.raw\npower')
    for n in ('a','b'):assert (shelf/f'{n}.raw').read_bytes()==(shelf/f'again-{n}.raw').read_bytes()
    assert len({p.read_bytes()[8:24] for p in shelf.glob('m*.tns')})==2
    assert not list(nested.glob('m*.tns')) and not list(ln.glob('m*.tns'))
    # A complete old reader folder may be transferred as one new section.
    old=root/'old';old.mkdir();shutil.copy(one,old/'part.folio.tns')
    previous=capture(old,'next\nnone\ndown')
    fresh=root/'upgraded';fresh.mkdir();old.rename(fresh/'Archive')
    v.run(fresh,'center\nnone 3\ncenter\nnone\nsnap imported.raw\npower')
    assert (fresh/'imported.raw').read_bytes()==previous and list(fresh.glob('m*.tns'))

def test_colour(root):
    size=(640,480);im=Image.new('RGB',size)
    im.putdata([((3*x+y)%256,(x+5*y)%256,(x//2+y)%256) for y in range(size[1]) for x in range(size[0])])
    path=root/'colour.png';im.save(path);book=root/'colour.folio.tns';v.prepare(path,book,'--compression','standard')
    _,raw,blocks=streams(book); assert len(blocks)==1 and len(blocks[0])==8
    assert all(mode in (8,9) for mode,_,_ in blocks[0])
    master=screen_color.words(im.tobytes())
    def mean(a,b):
        return (a&b)+(((a^b)&0xf7de)>>1)
    def blend(a,b,phase):
        mid=mean(a,b)
        return (mean(a,mid),mid,mean(mid,b))[phase]
    expected=[]
    for y in range(240):
        row=(80+4*y//3)*640
        for x in range(320):
            i=row+120+4*x//3
            expected.append(blend(blend(master[i],master[i+1],x%3),
                                  blend(master[i+640],master[i+641],x%3), y%3))
    assert v.render(book,80,1,120)[0]==struct.pack('<76800H',*expected)
    for mode,at,end in blocks[0]:
        assert screen_color.from_block(raw[at:end],640).width==640
    small=root/'tiny.png';im.resize((160,120)).save(small)
    sp=root/'tiny.folio.tns';v.prepare(small,sp);assert v.meta(sp)[2]==240 and v.meta(sp)[5]==0x102
    _,buf,blocks=streams(sp)
    assert any(m==10 for m,_,_ in blocks[0]), 'duplicated 2x2 pixels should use the smaller strip'
    for m,at,end in blocks[0]:
        if m==10: assert screen_color.from_block(buf[at:end],640).size==(640,64)
    fit=v.render(sp,0)[0]; zoom=v.render(sp,0,1)[0]
    assert fit[:2]==zoom[:2] and fit!=zoom
    again=root/'tiny-small.folio.tns';recompress(sp,again,'smallest');assert v.render(again,0,1)[0]

if __name__=='__main__':
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);b=test_pdf(root);print('PASS nested PDF outlines, same-page anchors, internal links, rotated crops, click selection and chapter subsets')
        a=test_gestures(root);print('PASS single, double and held centre clicks, shelf return and cold resume')
        test_sections(root,a,b);print('PASS sibling and nested shelves, duplicate filenames, independent bookmarks and moves')
        test_colour(root);print('PASS single 640-wide master, exact RGB565 detail pixels, upscaling and colour decoding')
