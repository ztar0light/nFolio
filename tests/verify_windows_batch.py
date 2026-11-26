"""Run with Windows Python from a Folio installation with the packer dependencies."""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pack


def run_launcher(folder, launcher, sources, keys, expected=0):
    # Use a separate working directory, as when Explorer starts a drop target.
    # Always quote each path, including the ones with spaces and shell characters.
    command = ' '.join(f'"{path}"' for path in [ROOT / launcher, *sources])
    command_line = f'"{os.environ["COMSPEC"]}" /d /v:off /s /c "{command}"'
    result = subprocess.run(command_line, cwd=folder, input=keys, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=180)
    log = result.stdout.decode(errors='replace')
    assert result.returncode == expected, (result.returncode,log)
    return log


def run_batch(folder, sources, keys):
    log = run_launcher(folder, 'Pack.cmd', sources, keys)
    assert log.count('Choose a compression preset') == 1, log
    assert log.count('Choose a resolution') == 1, log
    return log


def check(folder):
    shelves = [folder / 'Light Novels', folder / 'Manga & Art!']
    for shelf in shelves:
        shelf.mkdir()
    inputs = []
    for n in range(1, 11):
        path = shelves[n % 2] / f'Volume ({n:02}).txt'
        path.write_text(f'# Chapter {n}\n\nThe train left at dawn.\n', encoding='utf-8')
        inputs.append(path)
    art = shelves[1] / 'End pages.png'
    Image.new('RGB', (480, 640), (85, 119, 187)).save(art)
    run_launcher(folder, 'Wallpaper.cmd', [art], b'x')
    wall = (art.parent / 'background.tns').read_bytes()
    rgb565 = ((85>>3)<<11) | ((119>>2)<<5) | (187>>3)
    assert wall == struct.pack('<4sHH', b'FWP1', 320, 240) + struct.pack('<H', rgb565)*76800
    from verify_reader import prose_pdf
    pdf = shelves[0] / 'Book with a text layer.pdf'
    prose_pdf(pdf)
    log = run_batch(folder, [*inputs, art, pdf], b'21x')
    assert log.count('Smallest pack') == 12, log
    for source in [*inputs, art]:
        header = pack.HEADER.unpack_from(source.with_suffix('.folio.tns').read_bytes())
        assert header[0] == pack.comp.V3[pack.comp.PROFILES.index('smallest')]
        assert header[5] == (0x102 if source == art else 1)

    pdf_header = pack.HEADER.unpack_from(pdf.with_suffix('.folio.tns').read_bytes())
    assert pdf_header[0] == pack.comp.V4[2] and pdf_header[5] == 0x102

    prepared = shelves[0] / 'Ready to read.folio.tns'
    shutil.copyfile(ROOT / 'folio' / 'Manga' / 'TwinRails.folio.tns', prepared)
    log = run_batch(folder, [prepared, art], b'12x')
    assert log.count('Compact pack') == 2 and log.count('Standard Folio pack') == 1, log
    repacked = prepared.with_name('Ready to read-compact.folio.tns')
    assert pack.HEADER.unpack_from(repacked.read_bytes())[1] == pack.HEADER.unpack_from(prepared.read_bytes())[1]
    assert pack.HEADER.unpack_from(art.with_suffix('.folio.tns').read_bytes())[5] == 1


def check_recursive(folder):
    shelf = folder/'Dropped (books) & pages!'
    novels = shelf/'Novels'/'Series'
    novels.mkdir(parents=True)
    first = novels/'One.txt'
    first.write_text('At the station.',encoding='utf-8')
    other = folder/'Second dropped folder'
    other.mkdir()
    second = other/'巻02 อีกเล่ม.txt'
    second.write_text('On the next train.',encoding='utf-8')
    volume = shelf/'Manga'/'Volume 01'
    pages = volume/'Chapter 1'
    pages.mkdir(parents=True)
    for n,color in ((10,'red'),(2,'blue')):
        Image.new('RGB',(320,480),color).save(pages/f'{n}.png')
    log = run_batch(folder,[shelf,other,volume,pages/'2.png',first],b'22x')
    assert '3 packed, 0 failed.' in log,log
    assert set(shelf.rglob('*.folio.tns'))=={first.with_suffix('.folio.tns'),volume.with_suffix('.folio.tns')}
    assert second.with_suffix('.folio.tns').is_file()
    assert pack.HEADER.unpack_from(volume.with_suffix('.folio.tns').read_bytes())[3:6]==(1,2,1)
    (other/'Bad.pdf').write_bytes(b'broken')
    log = run_launcher(folder,'Pack.cmd',[other],b'21x',expected=1)
    assert '1 packed, 1 failed.' in log and 'Bad.pdf' in log
    ready = first.with_suffix('.folio.tns')
    log = run_launcher(folder,'Pack.cmd',[ready],b'2x')
    assert 'Choose a resolution' not in log and '1 packed, 0 failed.' in log


if __name__ == '__main__':
    if os.name != 'nt':
        sys.exit('Run this test with Windows Python.')
    with tempfile.TemporaryDirectory(prefix='Folio batch ') as directory:
        check(Path(directory))
        check_recursive(Path(directory))
    print('PASS Windows drag-and-drop: recursive and overlapping folders, one settings prompt, parallel manga and novels, failures and explicit repacks')
