"""Run the public Pack.cmd launcher with a POSIX shell on Linux or macOS."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from PIL import Image
import verify as v


def invoke(launcher, caller, paths, answers, env, expected=0, shell=None):
    # The launcher and the books are intentionally relative to the caller.
    relative = os.path.relpath(launcher, caller)
    if shell is None:
        command = ['/bin/sh', relative, *paths]
    else:
        command = [shell, '-c', '"$@"', 'nfolio-test', relative, *paths]
    result = subprocess.run(command, cwd=caller, input=answers, text=True,
                            encoding='utf-8', capture_output=True, env=env, timeout=120)
    assert result.returncode==expected, (result.returncode,result.stdout,result.stderr)
    return result.stdout+result.stderr


def shim(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\n'+body+'\n', encoding='utf-8')
    path.chmod(0o755)


def check(root):
    repo = root/"Reader & tools (test)"
    repo.mkdir()
    for name in ('Pack.cmd','pack_batch.py','pack.py','pdf_nav.py','pdf_text.py',
                 'folio_compression.py','screen_color.py','fonts'):
        source = v.ROOT/name
        if source.is_dir():
            shutil.copytree(source,repo/name)
        else:
            shutil.copy2(source,repo/name)
    launcher = repo/'Pack.cmd'
    caller = root/'My books'
    pages = caller/'Manga & scans!'/'Vol 01'/'Chapter 02'
    pages.mkdir(parents=True)
    for i,color in [(2,'red'),(10,'blue')]:
        Image.new('RGB',(320,480),color).save(pages/f'{i}.png')
    novel = caller/"-巻 01 (ต้น)'s.txt"
    novel.write_text('# One\n\nThe first train.',encoding='utf-8')
    bin_dir = root/'test-bin'
    shim(bin_dir/'python3','exec '+shlex.quote(sys.executable)+' "$@"')
    env = {**os.environ,'PATH':str(bin_dir),'FOLIO_JOBS':'2'}
    paths = [novel.name,'Manga & scans!',str(pages.relative_to(caller))]
    # GitHub's browser uploader and some ZIP tools can drop executable bits.
    launcher.chmod(0o644)
    log = invoke(launcher,caller,paths,'2\n1\n',env)
    assert log.count('Choose a compression preset')==log.count('Choose a resolution')==1
    assert '2 packed, 0 failed.' in log and 'with 2 worker(s)' in log
    manga = pages.parent.with_name('Vol 01.folio.tns')
    assert v.meta(manga)[3:6]==(1,2,0x102)
    title = novel.with_suffix('.folio.tns')
    assert v.meta(title)[0]==v.pack.comp.V3[v.pack.comp.PROFILES.index('smallest')]
    assert {manga,title}==set(caller.rglob('*.folio.tns'))
    print('PASS sh Pack.cmd from a different working directory, mixed recursive inputs, quoted paths and a non-executable checkout')

    # Mimic the interpreter at the documented project venv path. Make the
    # global python3 fail, so a successful conversion proves the local choice.
    shim(repo/'.venv/bin/python','exec '+shlex.quote(sys.executable)+' "$@"')
    shim(bin_dir/'python3','exit 76')
    launcher.chmod(0o755)
    for shell in dict.fromkeys(filter(None,('/bin/sh',shutil.which('bash'),shutil.which('zsh')))):
        log = invoke(launcher,caller,[novel.name],'1\n2\n',env,shell=shell)
        assert '1 packed, 0 failed.' in log
    assert 'Usage:' in invoke(launcher,caller,[],'',env,expected=1)
    (caller/'Broken.pdf').write_bytes(b'broken PDF')
    log = invoke(launcher,caller,['Broken.pdf',novel.name],'2\n1\n',env,expected=1)
    assert '1 packed, 1 failed.' in log and 'Broken.pdf' in log
    assert not (caller/'Broken.folio.tns').exists()
    (repo/'.venv/bin/python').unlink()
    (bin_dir/'python3').unlink()
    assert 'Python 3.10' in invoke(launcher,caller,[novel.name],'',env,expected=1)
    print('PASS direct launch from installed shells, project venv, missing interpreter, usage and failed-book exit status')


if __name__=='__main__':
    if os.name!='posix':
        sys.exit('Run this launcher test on Linux or macOS.')
    with tempfile.TemporaryDirectory() as tmp:
        check(Path(tmp))
