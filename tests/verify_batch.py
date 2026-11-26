"""Check recursive Folio discovery, parallel conversions and output ownership."""
import contextlib
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from unittest import mock
import zipfile

from PIL import Image,ImageFont
import verify as v
import pack_batch as batch
from verify_navigation import navigation_pdf


def page(path, color):
    path.parent.mkdir(parents=True,exist_ok=True)
    Image.new('RGB',(320,480),color).save(path)
    return path


def run(*args, expected=0):
    result = subprocess.run([sys.executable,str(v.ROOT/'pack_batch.py'),*map(str,args)],
                            encoding='utf-8',errors='replace',capture_output=True)
    assert result.returncode==expected,(result.returncode,result.stdout,result.stderr)
    return result.stdout


def library_test(root):
    library = root/'Dropped shelves!'
    novels = library/'Light Novels'
    novels.mkdir(parents=True)
    manga = library/'Manga & Art'/'Series'
    one, two = manga/'Vol. 1', manga/'Vol. 2'
    first = page(one/'Ch. 2'/'10.png','red')
    page(one/'Ch. 2'/'2.png','blue')
    page(one/'Chapter 10'/'1.png','yellow')
    page(two/'Pages'/'01.png','purple')
    page(two/'Pages'/'02.png','pink')
    pdf = novels/'Book.pdf'
    navigation_pdf(pdf)
    epub = v.make_epub(novels,3).rename(novels/'Book.epub')
    text = novels/'Book.pdf.txt'  # Reserve the first proposed name for a collision.
    text.write_text('# A separate novel\n\nA line of prose.',encoding='utf-8')
    cbz = manga/'Vol. 3.CBZ'
    with zipfile.ZipFile(cbz,'w') as archive:
        archive.write(first,'01.png')
    other = library/'別の本'
    other.mkdir()
    zipped = other/'Separate.zip'
    shutil.copyfile(cbz,zipped)
    hidden = page(manga/'.cache'/'fake.png','white')
    page(manga/'__MACOSX'/'fake.png','black')
    page(novels/'folder.jpg','green')
    (other/'notes.nfo').write_text('Comic metadata.')
    if os.name!='nt':
        (manga/'loop').symlink_to(library,target_is_directory=True)
        (manga/'alias.cbz').symlink_to(cbz)
    inputs = [library,novels,one,first,pdf,cbz,library]
    found, problems = batch.discover(inputs)
    assert not problems
    assert {s.path for s in found}=={one,two,epub,pdf,text,cbz,zipped}
    assert hidden not in next(s for s in found if s.path==one).pages
    log = run('--compression','smallest','-j','3',*inputs)
    assert 'with 3 worker(s)' in log and '7 packed, 0 failed.' in log
    expected = [(one,manga/'Vol. 1.folio.tns'), (two,manga/'Vol. 2.folio.tns'),
                (epub,novels/'Book.epub.folio.tns'), (pdf,novels/'Book.pdf.2.folio.tns'),
                (text,novels/'Book.pdf.folio.tns'), (cbz,manga/'Vol. 3.folio.tns'),
                (zipped,other/'Separate.folio.tns')]
    assert set(library.rglob('*.folio.tns'))=={output for _,output in expected}
    ref = root/'reference.folio.tns'
    for source,output in expected:
        v.prepare(source,ref,'--compression','smallest',*(['--title',source.name] if source.is_dir() else []))
        assert output.read_bytes()==ref.read_bytes(),source
    assert v.meta(expected[0][1])[3:5]==(2,3)
    assert v.meta(expected[1][1])[3:5]==(1,2)
    again,problems = batch.discover(inputs)
    assert not problems and [s.path for s in again]==[s.path for s in found]
    print('PASS nested mixed library, image folders, natural chapter/page order, duplicates, hidden entries and same-stem destinations')


def numbered_and_mixed(root):
    folder = root/'Numbers'
    page(folder/'01 - Beginning'/'1.png','red')
    page(folder/'02'/'1.png','blue')
    found,problems = batch.discover([folder])
    assert not problems and len(found)==1 and found[0].path==folder and len(found[0].pages)==2
    found,problems = batch.discover([folder/'01 - Beginning',folder/'02'])
    assert not problems and len(found)==2

    mixed = root/'Mixed v1.2'
    first = page(mixed/'10.png','red')
    second = page(mixed/'2.png','green')
    novel = mixed/'Article.txt'
    novel.write_text('Small story.',encoding='utf-8')
    nested = page(mixed/'Independent'/'01.png','blue')
    run('--compression','standard',mixed,first,nested)
    # Auto must still give manga a 640-wide master when prose is next to it.
    book = mixed.with_name(mixed.name+'.folio.tns')
    assert v.meta(book)[3:6]==(1,2,0x102)
    assert v.meta(mixed/'Independent.folio.tns')[4]==1
    assert v.meta(novel.with_suffix('.folio.tns'))[5]==1
    reference = root/'separate' / mixed.name
    reference.mkdir(parents=True)
    for p in (first,second): shutil.copyfile(p,reference/p.name)
    output = root/'direct.folio.tns'
    v.prepare(reference,output,'--compression','standard','--title',mixed.name)
    assert book.read_bytes()==output.read_bytes()

    # Many downloads wrap the actual pages one directory below each chapter.
    nested = root/'A one-shot'
    page(nested/'Chapter 1'/'Scans'/'2.png','black')
    page(nested/'Chapter 2'/'Pages'/'1.png','white')
    (nested/'Chapter 2'/'Extra.txt').write_text('A standalone story.',encoding='utf-8')
    run('--fit-only',nested)
    wrapped = nested.with_name(nested.name+'.folio.tns')
    assert v.meta(wrapped)[3:5]==(2,2) and (nested/'Chapter 2'/'Extra.folio.tns').is_file()
    font = ImageFont.truetype(str(v.pack.DEFAULT_FONT),15)
    raw = wrapped.read_bytes()
    offset = v.meta(wrapped)[6]
    for i in (1,2):
        label = v.pack.label_bits(f'Chapter {i}',font)
        assert struct.unpack_from('<I',raw,offset+4)[0]==i-1
        assert raw[offset+8:offset+8+len(label)]==label
        offset += 8+len(label)

    # An inaccessible chapter must not produce an incomplete replacement.
    real_scandir = os.scandir
    def denied(p):
        if Path(p)==nested/'Chapter 1':
            raise PermissionError(f'Cannot read {p}')
        return real_scandir(p)
    with mock.patch.object(batch.os,'scandir',denied):
        found,issues = batch.discover([nested])
    assert issues and nested not in {s.path for s in found}


def failure_and_repack(root):
    folder = root/'recovery'
    folder.mkdir()
    bad = folder/'Broken.pdf'
    bad.write_bytes(b'This is not a PDF')
    old = bad.with_suffix('.folio.tns')
    shutil.copyfile(v.ROOT/'folio/Manga/TwinRails.folio.tns',old)
    before = old.read_bytes()
    good = folder/'Good.txt'
    good.write_text('This book should still finish.',encoding='utf-8')
    log = run('-j','2',folder,expected=1)
    assert '1 packed, 1 failed.' in log and f'FAILED {bad}' in log
    assert old.read_bytes()==before and good.with_suffix('.folio.tns').exists()
    assert not list(folder.glob('.folio-*'))
    bad.unlink()
    # Explicitly selected prepared input has priority over a same-stem output.
    collision = folder/'Broken.txt'
    collision.write_text('Different source.',encoding='utf-8')
    log = run('--compression','smallest','--fit-only',folder,old)
    assert '3 packed, 0 failed.' in log
    assert old.read_bytes()==before and (folder/'Broken.txt.folio.tns').is_file()
    assert v.meta(folder/'Broken-smallest.folio.tns')[1]==v.meta(old)[1]
    assert not list(folder.glob('*-smallest-smallest.folio.tns'))
    only = root/'only-output'
    only.mkdir()
    shutil.copyfile(old,only/'ready.folio.tns')
    assert 'No source books' in run(only,expected=1)
    print('PASS numbered chapters, mixed pictures and prose, bad-book isolation, atomic replacement and explicit repacking')


def test_workers():
    lock = threading.Lock()
    arrived = threading.Barrier(2,timeout=5)
    active = peak = 0
    def convert(source, output, profile, fit):
        nonlocal active,peak
        with lock:
            active += 1
            peak = max(peak,active)
        arrived.wait()
        with lock:
            active -= 1
        return 0,'done'
    sources = [batch.Source(Path(str(i))) for i in range(4)]
    with mock.patch.object(batch,'convert',convert), contextlib.redirect_stdout(io.StringIO()):
        assert batch.run_batch(sources,[s.path for s in sources],'smallest',False,2)==0
    assert peak==2
    print('PASS independent conversions overlap and honour the worker limit')


if __name__=='__main__':
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        library_test(root)
        numbered_and_mixed(root)
        failure_and_repack(root)
        test_workers()
