"""Exercise spread navigation, the centre fold, and the new storage encodings."""
from __future__ import annotations
import contextlib, io, random, shutil, struct, tempfile
from pathlib import Path
from PIL import Image
import verify as v
from make_samples import NOVEL, create
from verify import pack
import folio_compression as comp


def capture(folder, actions, out='view.raw'):
    v.run(folder, actions+'\nnone\nsnap '+out+'\npower')
    return (folder/out).read_bytes()


def marker(folder):
    _,state,path=v.marks(folder)[-1]
    return state,path.read_bytes()[34]


def leaf_source(root):
    directory=root/'scan'; directory.mkdir()
    # Two source pages at exactly twice final spread resolution. Make every
    # row and both columns adjacent to the fold independently recognizable.
    artwork=Image.frombytes('L',(320,240),bytes(17*((x//9+y//3+(x>157)*3)%16) for y in range(240) for x in range(320)))
    for index,(l,r) in enumerate(((160,320),(0,160)),1):
        artwork.crop((l,0,r,240)).resize((320,480),Image.Resampling.NEAREST).save(directory/f'{index}.png')
    for i,c in ((3,51),(4,170),(5,238)):
        Image.new('L',(320,480),c).save(directory/f'{i}.png')
    return directory,artwork


def frame(im):
    return struct.pack('<76800H',*(v.rgb(c) for c in im.convert('L').tobytes()))


def solid_halves(left,right):
    a=Image.new('L',(320,240),left); a.paste(right,(160,0,320,240));return frame(a)


def run_spreads(root):
    directory,art=leaf_source(root); device=root/'reader';device.mkdir()
    p=device/'manga.folio.tns';v.prepare(directory,p,'--fit-only')
    picture=frame(art)
    assert capture(device,'spread')==picture, 'first two pages must reconstruct every centre-fold pixel'
    swapped=Image.new('L',(320,240));swapped.paste(art.crop((160,0,320,240)),(0,0));swapped.paste(art.crop((0,0,160,240)),(160,0))
    assert capture(device,'order')==frame(swapped); assert marker(device)[1]&2
    assert capture(device,'right')==solid_halves(51,170), 'LTR right turns forward two pages'
    assert capture(device,'left\nnone\norder')==picture
    assert capture(device,'left')==solid_halves(170,51), 'RTL left turns forward two pages'
    last=Image.new('L',(320,240),255); last.paste(238,(80,0,240,240))
    assert capture(device,'next')==frame(last), 'odd last page honours its portrait aspect and is centred'
    assert capture(device,'next')==frame(last)
    assert capture(device,'right')==solid_halves(170,51)
    assert capture(device,'minus')==picture
    first=Image.new('L',(320,240),255); first.paste(art.crop((160,0,320,240)),(80,0))
    assert capture(device,'pair')==frame(first), 'standalone cover offset'
    paired=Image.new('L',(320,240),51); paired.paste(art.crop((0,0,160,240)),(160,0))
    assert capture(device,'next')==frame(paired)
    # Cold relaunch after a checkpoint retains the pairing phase and orientation.
    v.run(device,'order\nnone 45\nsnap precut.raw\ncut',73)
    assert capture(device,'none')==(device/'precut.raw').read_bytes()
    assert marker(device)[1] & 15 == 15
    solo=root/'toggle';solo.mkdir();shutil.copy(p,solo/p.name)
    before=capture(solo,'down\nnone\nzoom\nnone\nright\nnone\ndown');state=marker(solo)[0]
    assert capture(solo,'spread')==picture
    assert capture(solo,'spread')==before and marker(solo)[0]==state
    # Entering from source page 2 should join pages 2 and 3 on first use.
    anchored=root/'anchored';anchored.mkdir();shutil.copy(p,anchored/p.name)
    assert capture(anchored,'plus\nnone\nspread')==frame(paired)
    return p,picture


def run_wide(root):
    source=root/'wide';source.mkdir()
    for i,(h,color) in enumerate(((480,34),(480,51),(180,153),(480,170),(480,204)),1): Image.new('L',(320,h),color).save(source/f'{i}.png')
    dest=root/'wide-device';dest.mkdir();v.prepare(source,dest/'wide.folio.tns','--fit-only')
    assert capture(dest,'spread')==solid_halves(51,34)
    wide=Image.new('L',(320,240),255);wide.paste(153,(0,30,320,210))
    assert capture(dest,'next')==frame(wide), 'prejoined art must occupy the entire 320px width'
    assert capture(dest,'plus')==solid_halves(204,170)
    assert capture(dest,'prev')==frame(wide)
    combined=Image.new('L',(320,240),255);combined.paste(170,(0,0,160,240)); combined.paste(153,(160,0,320,90))
    assert capture(dest,'wide')==frame(combined), 'manual pairing of a landscape half and a portrait half'


def streams(p):
    raw=p.read_bytes(); h=pack.HEADER.unpack_from(raw); result=[]
    for level,index in ([(1,h[8])] if h[5]==0x102 else enumerate(h[8:8+h[5]])):
        n=((h[2]<<level)+63)//64
        table=struct.unpack_from(f'<{n+1}I',raw,index)
        result.append([(raw[a],a,b) for a,b in zip(table,table[1:])])
    return h,raw,result


def old_file(standard,dest):
    h,raw,_=streams(standard); old=bytearray(raw[:-8]); old[:8]=comp.V1
    for i in range(h[4]):
        at=h[7]+i*4
        struct.pack_into('<I',old,at,struct.unpack_from('<I',old,at)[0]&~comp.WIDE)
    dest.write_bytes(old)


def recompress(src,p,profile):
    with contextlib.redirect_stdout(io.StringIO()): pack.main([str(src),'-o',str(p),'--compression',profile])


def run_compression(root):
    txt=root/'Test novel.txt';txt.write_text(NOVEL,encoding='utf-8')
    files={name:root/f'{name}.folio.tns' for name in comp.PROFILES}
    for name,p in files.items():v.prepare(txt,p,'--compression',name)
    h,raw,blocks=streams(files['compact']); index=h[8]; table_end=index+4*(len(blocks[0])+1)
    assert struct.unpack_from('<I',raw,table_end)[0]&comp.LZ and any(m==4 for m,_,_ in blocks[0])
    assert len({v.meta(p)[1] for p in files.values()})==1
    assert files['smallest'].stat().st_size<files['compact'].stat().st_size<files['standard'].stat().st_size
    for y in (0,49,301,510,1026):
        assert v.render(files['compact'],y)[0]==v.render(files['standard'],y)[0], 'compact prose must be pixel-identical'
        assert v.render(files['smallest'],y)[0]
    legacy=root/'original.folio.tns';old_file(files['standard'],legacy);original=legacy.read_bytes()
    converted=root/'restored.folio.tns';recompress(legacy,converted,'compact');assert original==legacy.read_bytes()
    assert converted.read_bytes()==files['compact'].read_bytes(), 'packed input and source conversion share prose encodings'
    again=root/'again.folio.tns';recompress(converted,again,'compact');assert again.read_bytes()==converted.read_bytes()
    old_device=root/'upgrade';old_device.mkdir();original_path=old_device/'mybook.folio.tns';shutil.copy(legacy,original_path)
    before=capture(old_device,'next\nnone\ndown');shutil.copy(converted,original_path);assert capture(old_device,'none')==before
    art=root/'repeated';art.mkdir(); repeated=Image.frombytes('L',(320,64),bytes(17*((x//6+y)%16) for y in range(64) for x in range(320)))
    sheet=Image.new('L',(320,64*20));
    for y in range(0,sheet.height,64): sheet.paste(repeated,(0,y))
    sheet.save(art/'1.png'); com=root/'repeat.folio.tns';v.prepare(art,com,'--fit-only')
    _,data,blocks=streams(com); assert sum(m==7 for m,_,_ in blocks[0])==19
    assert v.render(com,763)[0]==frame(sheet.crop((0,763,320,1003)))
    damaged=root/'damaged';damaged.mkdir();bad=bytearray(data); at=blocks[0][1][1];struct.pack_into('<I',bad,at+1,1)
    (damaged/'bad.folio.tns').write_bytes(bad);assert 'book' in v.run(damaged,'esc',1)
    # A checksum fault in an otherwise valid font is caught before any glyph drawing.
    broken=bytearray(raw); broken[table_end+4]^=0x40
    (damaged/'bad.folio.tns').write_bytes(broken);assert 'font' in v.run(damaged,'esc',1)
    noise=root/'tones.png';rng=random.Random(39)
    pixels=bytes(rng.randrange(16)*17 for _ in range(320*256))
    Image.frombytes('L',(320,256),pixels).save(noise);tonebook=root/'tones.folio.tns';v.prepare(noise,tonebook,'--fit-only','--compression','smallest')
    assert set(m for m,_,_ in streams(tonebook)[2][0])=={6}
    expected=Image.frombytes('L',(320,240),bytes(comp.FOUR[g>>4]*85 for g in pixels[:320*240]))
    assert v.render(tonebook)[0]==frame(expected), '4-shade manga decoder must match desktop quantizer'
    # Packed inputs may be copied to new files. A request to put the result on top of the input is rejected.
    dest=root/'copy-small.folio.tns'; recompress(converted,dest,'smallest');assert v.meta(dest)[1]==v.meta(legacy)[1]
    try:recompress(dest,again,'standard')
    except ValueError:pass
    else:raise AssertionError('Quality upgrades need source artwork')
    assert again.read_bytes()==converted.read_bytes()

if __name__=='__main__':
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);run_spreads(root); print('PASS pixel-exact joined artwork, RTL/LTR, offset, odd pages, zoom anchor, cold spread bookmark')
        run_wide(root);print('PASS prejoined wide art, forced pairs, unequal page sizes')
        run_compression(root);print('PASS all storage presets, lossless compact prose, legacy conversion, saved positions, referenced strips, damaged input')
