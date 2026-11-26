"""Prepare a 320 × 240 RGB565 menu background for the Folio handheld reader."""
import argparse
import os
from pathlib import Path
import struct
import tempfile

from PIL import Image, ImageOps
from screen_color import words


def prepare_wallpaper(source, output):
    if source.resolve() == output.resolve():
        raise ValueError('Choose an output path different from the input image.')
    size = (320, 240)
    with Image.open(source) as image:
        rgba = ImageOps.exif_transpose(image).convert('RGBA')
        paper = Image.new('RGB', rgba.size, 'white')
        paper.paste(rgba, mask=rgba.getchannel('A'))
        fitted = ImageOps.fit(paper, size, method=Image.Resampling.LANCZOS)
    pixels = words(fitted.tobytes())
    data = struct.pack('<4sHH', b'FWP1', *size) + struct.pack(f'<{len(pixels)}H', *pixels)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(data)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('-o', '--output', type=Path, help='defaults to background.tns beside the image')
    args = parser.parse_args()
    output = args.output or args.image.with_name('background.tns')
    try:
        prepare_wallpaper(args.image, output)
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    print(output.resolve())


if __name__ == '__main__':
    main()
