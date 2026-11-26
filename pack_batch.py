"""Pack the individual Folio books in one or more files or directory trees."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

if sys.version_info < (3, 10):
    sys.exit('nFolio needs Python 3.10 or newer. See the desktop setup in README.md.')

import pack

BOOK_FILES = {'.epub', '.pdf', '.cbz', '.zip', '.txt'}
CHAPTER = re.compile(r'^(?:ch(?:apter|ap)?|ep(?:isode)?|part)(?=[\s._#-]|\d)', re.I)
NUMBERED_CHAPTER = re.compile(r'\d+(?:[._-]\d+)*(?:\s+[-–—]\s+.+)?')
VOLUME = re.compile(r'(?:^|[\W_])(?:vol(?:ume)?|book|v)[\s._#-]*\d', re.I)
COVERS = {'cover', 'folder', 'thumb', 'thumbnail'}
PACKER = Path(__file__).resolve().with_name('pack.py')


@dataclass
class Source:
    path: Path
    pages: list[Path] | None = None


def chapter_folder(name):
    if CHAPTER.match(name) or name.casefold() in ('prologue','epilogue'):
        return True
    if NUMBERED_CHAPTER.fullmatch(name) and not VOLUME.search(name):
        return True
    return False


def scan_folder(folder, errors):
    """Return books under this folder. Only labelled chapter groups merge upwards."""
    books, pictures, wrappers = [], [], []
    missing_pages = False
    try:
        with os.scandir(folder) as entries:
            children = sorted(entries, key=lambda entry: (pack.natural(entry.name),entry.name))
    except OSError as exc:
        errors.append(str(exc))
        return books
    for entry in children:
        if not pack.visible(entry.name):
            continue
        path = folder/entry.name
        try:
            if os.name=='nt':
                info = entry.stat(follow_symlinks=False)
                if info.st_file_attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM):
                    continue
                if info.st_reparse_tag==stat.IO_REPARSE_TAG_MOUNT_POINT:
                    continue
            if entry.is_dir(follow_symlinks=False):
                before = len(errors)
                for child in scan_folder(path,errors):
                    if child.path==path and child.pages and chapter_folder(entry.name):
                        pictures.extend(child.pages)
                    elif child.path==path and child.pages and entry.name.casefold() in pack.PAGE_FOLDERS:
                        wrappers.append(child)
                    else:
                        books.append(child)
                if len(errors)>before and (chapter_folder(entry.name) or entry.name.casefold() in pack.PAGE_FOLDERS):
                    missing_pages = True
            elif entry.is_file(follow_symlinks=False):
                suffix = path.suffix.lower()
                if suffix in BOOK_FILES:
                    books.append(Source(path))
                elif suffix in pack.PICTURES:
                    pictures.append(path)
        except OSError as exc:
            errors.append(str(exc))
            if path.suffix.lower() in pack.PICTURES or chapter_folder(entry.name) or entry.name.casefold() in pack.PAGE_FOLDERS:
                missing_pages = True
    # Keep "Volume 01/Chapter 2/Pages" together, including named one-shots
    # with a single Images folder. A mixed collection can have its own gallery.
    if not books or VOLUME.search(folder.name) or chapter_folder(folder.name):
        for child in wrappers:
            pictures.extend(child.pages)
    else:
        books.extend(wrappers)
    # A loose cover beside EPUBs or other volumes is often just folder artwork.
    if pictures and (not books or any(p.parent!=folder or p.stem.casefold() not in COVERS for p in pictures)):
        if missing_pages:
            errors.append(f'Skipped incomplete manga folder: {folder}')
        else:
            books.append(Source(folder, pictures))
    return books


def discover(inputs):
    folders, files, errors = set(), set(), []
    for raw in inputs:
        try:
            path = Path(raw).resolve(strict=True)
            if path.is_dir():
                folders.add(path)
            elif path.is_file() and (path.suffix.lower() in BOOK_FILES|pack.PICTURES or pack.prepared_book(path)):
                files.add(path)
            else:
                errors.append(f'Unsupported input: {path}')
        except (OSError,RuntimeError) as exc:
            errors.append(str(exc))
    sources = {}
    for root in sorted(folders):
        if any(parent in folders for parent in root.parents):
            continue
        for book in scan_folder(root,errors):
            sources[book.path] = book
            if book.pages:
                files.difference_update(book.pages)
    for path in files:
        sources.setdefault(path,Source(path))
    return sorted(sources.values(),key=lambda s:(pack.natural(s.path),str(s.path))), errors


def output_key(path):
    return str(path.resolve()).casefold()


def plan_outputs(sources, profile):
    """Reserve every input and preferred destination before workers can write."""
    preferred = [pack.default_output(s.path,profile) for s in sources]
    counts = Counter(output_key(path) for path in preferred)
    inputs = {output_key(s.path) for s in sources}
    reserved = inputs | set(counts)
    outputs = []
    for source, dest in zip(sources,preferred):
        key = output_key(dest)
        if counts[key]>1 or key in inputs or dest.is_dir():
            kind = 'pages' if source.pages else source.path.suffix[1:].lower()
            base = dest.name[:-len('.folio.tns')] + '.' + kind
            dest = dest.with_name(base+'.folio.tns')
            number = 2
            while output_key(dest) in reserved or dest.is_dir():
                dest = dest.with_name(f'{base}.{number}.folio.tns')
                number += 1
            reserved.add(output_key(dest))
        outputs.append(dest)
    return outputs


def convert(source, dest, profile, fit_only):
    # Each thread launches a separate encoder. PDFium and its native state
    # are confined to that process, and Python encoders can use separate cores.
    command = [sys.executable, str(PACKER), str(source.path), '-o', str(dest), '--compression', profile]
    page_list = ''
    if source.pages:
        command.extend(['--pages-from-stdin','--title',source.path.name or 'Pages'])
        page_list = json.dumps([page.relative_to(source.path).as_posix() for page in source.pages])
    if fit_only and not pack.prepared_book(source.path):
        command.append('--fit-only')
    result = subprocess.run(command, input=page_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding='utf-8', errors='replace', env={**os.environ,'PYTHONIOENCODING':'utf-8'})
    return result.returncode, result.stdout.rstrip()


def worker_count():
    cpus = getattr(os,'process_cpu_count',os.cpu_count)() or 1
    return min(4,cpus)


def run_batch(sources, outputs, profile, fit_only, workers):
    workers = min(workers,len(sources))
    print(f'Packing {len(sources)} book(s) with {workers} worker(s).',flush=True)
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(convert,source,dest,profile,fit_only):source.path
                   for source,dest in zip(sources,outputs)}
        try:
            for i,future in enumerate(as_completed(futures),1):
                path = futures[future]
                try:
                    code, log = future.result()
                except OSError as exc:
                    code, log = 1, str(exc)
                if code:
                    failed += 1
                    log = log or f'Encoder exited with status {code}.'
                status = 'FAILED' if code else 'Packed'
                print(f'[{i}/{len(sources)}] {status} {path}\n{log}',flush=True)
        except KeyboardInterrupt:
            for future in futures:
                future.cancel()
            raise
    print(f'{len(sources)-failed} packed, {failed} failed.',flush=True)
    return failed


def choose(labels, prompt):
    for i,label in enumerate(labels,1):
        print(f'{i}. {label}',flush=True)
    digits = ''.join(str(n) for n in range(1,len(labels)+1))
    question = f'{prompt} [{"/".join(digits)}]: '
    if os.name=='nt':
        import msvcrt
        print(question,end='',flush=True)
        console = sys.stdin.isatty()
        while True:
            if console:
                key = msvcrt.getwch()
                if key in ('\x00','\xe0'):
                    msvcrt.getwch()
                    continue
            else:
                # Read only this answer. The next prompt and Pack.cmd's pause
                # may share the same redirected Windows input handle.
                raw = os.read(sys.stdin.fileno(),1)
                if not raw:
                    raise EOFError
                key = chr(raw[0])
            if key in ('\x03','\x1b'):
                raise KeyboardInterrupt
            if key in digits:
                print(key,flush=True)
                return int(key)
    while True:
        answer = input(question).strip()
        if answer in tuple(digits):
            return int(answer)


def positive_int(value):
    n = int(value)
    if n<1:
        raise argparse.ArgumentTypeError('worker count must be at least 1')
    return n


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs',nargs='+',type=Path)
    parser.add_argument('--compression',choices=pack.comp.PROFILES,default='compact')
    parser.add_argument('--fit-only',action='store_true')
    parser.add_argument('-j','--jobs',type=positive_int,default=os.environ.get('FOLIO_JOBS') or worker_count(),
                        help='parallel books (default up to four, or FOLIO_JOBS)')
    parser.add_argument('--interactive',action='store_true',help='ask once for the batch settings')
    args = parser.parse_args(argv)
    if args.interactive:
        print('Looking for books...',flush=True)
    sources, errors = discover(args.inputs)
    for error in errors:
        print(f'Folio: {error}',file=sys.stderr,flush=True)
    if not sources:
        print('No source books or manga pages found. To recompress a .folio.tns, drop that file directly.')
        return 1
    if args.interactive:
        print('Choose a compression preset for this batch.')
        choice = choose(('Compact   - crisp prose and smaller RGB565 artwork',
                         'Smallest  - reuse PDF letters and compress artwork',
                         'Standard  - preserve the full screen-colour artwork'),'Preset')
        args.compression = ('compact','smallest','standard')[choice-1]
        if any(not pack.prepared_book(s.path) for s in sources):
            print('Choose a resolution for source books in this batch.')
            args.fit_only = choose(('Auto - 640-pixel artwork for zoom detail',
                                    'Fit only - 320 pixels for smaller files'),'Resolution')==2
    outputs = plan_outputs(sources,args.compression)
    failed = run_batch(sources,outputs,args.compression,args.fit_only,args.jobs)
    return int(bool(failed or errors))


if __name__=='__main__':
    sys.stdout.reconfigure(errors='backslashreplace')
    try:
        sys.exit(main())
    except (KeyboardInterrupt,EOFError):
        sys.exit('Folio batch cancelled.')
