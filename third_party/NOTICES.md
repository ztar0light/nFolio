# Third-party components

| Component | Use and provenance |
| --- | --- |
| Ndless SDK | The launcher uses the public LCD, keypad and filesystem APIs. The source used for this release is the Ndless project at commit `b88e4af2f74f10f3b28c9f7290a79de8fd9108b8`. The MPL 1.1 text is in `notices/Ndless-MPL-1.1.html`. [SDK source](https://github.com/ndless-nspire/Ndless/tree/b88e4af2f74f10f3b28c9f7290a79de8fd9108b8/ndless-sdk) |
| ARM GNU Toolchain | GCC runtime and newlib from the Arm 13.3.Rel1 distribution. The distribution's complete notices are in `notices/Arm-toolchain-licenses.txt`. [Toolchain](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads) |
| miniz 3.0.2 | The reader uses the inflate decoder only. Upstream source headers and the MIT license are in `miniz`. [Source](https://github.com/richgel999/miniz/tree/3.0.2) |
| 7-Zip LZMA decoder 24.09 | Igor Pavlov’s public-domain decoder expands the font atlas at book open. Original source and notices are in `lzma`. [Source](https://github.com/ip7z/7zip/tree/24.09/C) |
| stb_image | JPEG decoding from commit `f0569113c93ad095470c54bf34a17b36646bbbb5`. The MIT license and public-domain alternative are included in `stb_image.h`. [Source](https://github.com/nothings/stb/blob/f0569113c93ad095470c54bf34a17b36646bbbb5/stb_image.h) |
| DejaVu | The packer includes DejaVu Serif. Small interface glyphs are rasterized from DejaVu Sans Mono. See `fonts/LICENSE.txt` at the package root. [Fonts](https://dejavu-fonts.github.io/) |

The example stories and the Night Signal and Twin Rails artwork were created for Folio.
