# Reader and packer guide

See the [nFolio README](../README.md) for desktop setup and calculator installation.

## Organize the shelf

Each folder inside **folio** appears as a section. You can nest sections, leave books directly in **folio**, or give books in different sections the same filename. Move or rename a book and Folio follows its internal ID. Open the launcher again after changing files on the calculator to refresh the shelf.

| Handheld path under My Documents | Appears on the shelf |
| --- | --- |
| folio/Folio.tns | Launcher |
| folio/background.tns or folio/background.folio.tns | Hidden menu wallpaper |
| folio/My Book.folio.tns | My Book at the top level |
| folio/Light Novels/PlatformSix.folio.tns | Platform Six in Light Novels |
| folio/Manga/Series/Volume 1.folio.tns | Volume 1 inside Manga → Series |

**Esc**, **Home**, or **B** returns from reading to the current book's section. Highlight a book to see its cover and saved page. A declared EPUB cover can live outside the reading order. Books without a separate cover use the first page, or the top of the first long comic strip. **Esc** goes up a folder and exits at the root. Choose a title to resume its own position, zoom, pan, reading mode and spread settings. A fresh launch reopens the last title automatically when it is still present.

## Touchpad and reading controls

| Control | While reading |
| --- | --- |
| **× / ÷** | Increase or decrease the centred margin trim by 0.05× per press, from 1.00× to 2.00×. Folio remembers it for this book. |
| **Centre click** | Toggle 1.5×. A second single click restores the single-page trim or fits the whole spread. Folio allows about 0.4 seconds after release for a double-click. |
| **Double centre click** | Press again inside that interval to toggle facing pages from the current reading position. Your finger can stay on the pad between clicks. |
| **Ctrl + glide** | Show and move the pointer. Release Ctrl to hide it. Lift and reposition your finger to move farther. |
| **Ctrl + click on a PDF link** | Jump to its embedded destination. |
| **Ctrl + centre click away from a link** | Switch between paged and continuous scrolling. From facing pages, return to continuous scrolling. |
| **Menu** | Open the reader menu. From PDF bookmarks or chapters, press Menu to return to the reader menu. Press it again to resume reading. |
| **Up / Down**, or **8 / 2** | Move six screen pixels. Single-page reading stops at the page boundary in paged mode. In a magnified spread, pan vertically. Hold for a slower, steady scroll after a short delay. |
| **Left / Right**, or **4 / 6** | Turn a page or a fitted pair. In a 1.5× view, pan sideways. |
| **Space / Del** | Move forward / back with overlap, or turn one pair in a spread. |
| **+ / −** | Turn a source page or a pair, including while zoomed. |
| **Z / D** | Direct zoom / facing-page shortcuts. |
| **Tab / S / On** | Show reading information / save position / save and exit. |

For a novel with wide margins, press **×** four times from fit width for **1.20×**. This crops about 27 of the original 320 pixels from each side. The scale appears briefly after each press. The same trim follows page turns, continuous scrolling and restarts. Opening a spread fits both pages and keeps the single-page trim in memory. Pressing **×** or **÷** in single-page detail view returns to your book’s trim and adjusts it by one step.

Aim for the small oval in the middle to zoom or toggle facing pages. It spans one fifth of the pad’s width and height. The surrounding area acts as the four arrows. A directional press keeps its direction if your thumb slides across the centre. Folio ignores a press if the touchpad never reports where it landed.

In the bookshelf, reader menu and bookmarks or chapters list, Up or Left moves one item back and Down or Right moves one item forward. A held direction moves once. Release and press again for the next item. Space and Del move by a whole list screen. Centre or Enter selects and Esc goes back. Hold Ctrl to point and click a row or the Back, Previous and Next buttons.

Facing pages default to right-to-left. **R** changes the reading direction, **P** adjusts the pairing by one page, and **W** includes landscape scans in pairs. A prejoined landscape scan normally fills the available width and can use its 640-pixel master when magnified. **Centre** or **Z** magnifies the spread to **1.5×** around the fold. All four arrows then pan across the artwork. **+ / −** or **Space / Del** turns the pair and recentres the view. Click once again for the whole spread, or double-click the centre or press **D** to return to your single-page view. A cold restart can also resume a magnified spread at the same pan.

Folio checkpoints after about 0.7 seconds without movement and periodically during a held scroll. Leaving a book commits its latest position. Two alternating records protect the previous checkpoint during an interrupted write. A sudden reset can lose the movement since the last checkpoint.

## Menu backgrounds and dark mode

Press **Menu** on the shelf, or choose **Menu → Appearance** while reading. Highlight **Background** or **Theme** and press **Enter** or **Centre** to cycle its choices. Both settings apply across books and survive a restart.

| Background | What appears behind the menus |
| --- | --- |
| Plain | Solid paper. This is the default. |
| Last book | Cover of the most recently opened book. |
| Selected book | Cover of the highlighted book on the shelf, or the open book while reading. Folders use the last book’s cover. |
| Custom file | Your own picture or a book cover stored directly in **My Documents/folio**. |

For a custom picture on Windows, drop an image onto **Wallpaper.cmd**. On Linux and macOS, run `./.venv/bin/python wallpaper.py "picture.jpg"` from the project folder after the desktop setup. Copy the resulting **background.tns** from beside the image into **My Documents/folio**. The converter centre-crops to 320 × 240. To use a particular book’s cover, copy its pack into that folder as **background.folio.tns**. The standalone picture takes priority if both files exist. Neither appears on the shelf. Restart Folio after replacing a background file.

**Light** is the default theme. **Dark menus** keeps book artwork in its original colours. **Dark menus and pages** also inverts the reading page, including illustrations. All menu backgrounds are tinted to keep labels legible. With Ctrl held, the shelf highlight follows the pointer immediately and the cover preview changes when the pointer rests.

## Click PDF bookmarks

Open **PDF → Station Atlas**. Hold **Ctrl**, glide until the pointer is over **The survey mark**, then click while still holding Ctrl. The link lands at the teal line inside the next page. To open the embedded outline, press **Menu**, choose **Bookmarks / chapters**, then use the arrows and Enter or hold Ctrl and click a row. Indentation shows nested bookmarks.

The packer imports internal PDF destinations from outlines and on-page link annotations, including many contents pages. It preserves multiple destinations on one page, rotated or cropped page coordinates, and links across pages while zoomed or in spread view. Selecting a chapter range drops links whose destinations are outside the pack. External websites and linked files are outside the offline reader. A printed contents page needs an actual PDF link annotation for its entries to be clickable. Repack the **original PDF** to add destinations missing from older books.

## Compression and resolution

Start from the original PDF when packing a light novel with **Smallest**. It can reuse the text layer’s letter shapes and positions. An older raster pack can be recompressed from its existing pixels.

| Preset | Encoding |
| --- | --- |
| **Standard** | For colour art, preserve the resized image after conversion to RGB565. Store the smallest exact native encoding tried for each strip. Prose and monochrome scans use the original 16-shade rendering. |
| **Compact** | Preserve prose and 16-shade grayscale. Colour strips may use a smaller palette. Identical strips can share storage. |
| **Smallest** | Reuse letter shapes on text-only PDF pages when smaller. Grayscale uses four shades and the text encoder retains coloured ink in RGB565. Artwork can use a smaller palette or JPEG. |

Colour uses the handheld's **RGB565** format with 5 red, 6 green and 5 blue bits. The packer compares direct pixels, row differences and indexed screen-colour palettes. It can store repeated 2×2 pixels once. A comic or PDF normally stores one **640-pixel-wide** master from which the reader makes the 320-wide view. A 4:3 illustration uses a 640 × 480 master. Portrait pages and long strips retain their aspect ratio. Low-resolution art can be enlarged and dense scans are downsampled. The 1.5× view samples about 427 × 320 master pixels for the 320 × 240 display.

**Fit only** stores a 320-wide image. The reader can still magnify its pixels. Illustrated EPUBs keep the inline layout. **--detail** additionally renders high-resolution prose. Other options include **--gray**, **--chapters 3-7**, **--font**, **--font-index**, **--font-size 12–30** and **--quality 1–95** for the colour palette and JPEG quality. Recompressing an existing book preserves its ID. Preparing the original with a different layout or explicit conversion settings can create a new ID.

In a 346-page PDF light novel with one illustrated cover, Smallest + Auto produced a **1,173,621-byte** book from a **1,383,793-byte** PDF. The same conversion with Folio 0.3.2 used **6,643,691 bytes**. The page positions, PDF navigation and book ID matched across both packs. Scanned novels and image-only manga depend on artwork compression. The packer reports the actual output size against the input and the Standard Folio pack.

## Input formats

EPUB 2 and 3 follow spine order and chapter links and keep embedded raster illustrations. Horizontal prose supports a user-supplied font, including suitable Japanese, Chinese or Thai fonts. The converter keeps ruby base text. EPUB CSS, vertical typesetting and vector-only EPUB artwork are outside this version. UTF-8 TXT accepts paragraphs separated by blank lines and chapter headings beginning with **#**. Folders of TXT files use natural filename order.

CBZ/ZIP, folders of images, and folders of CBZ volumes are supported. JPEG, PNG, WebP, BMP, TIFF and the first GIF frame are accepted. PDF import uses PDFium on the desktop.

## Batch and single-book options

Set the environment variable `FOLIO_JOBS` to choose the worker count used by `Pack.cmd`. The batch scanner skips hidden folders, directory links and previously generated packs. If two sources in a batch would produce the same filename, it includes the source type, for example `Vol 01.pdf.folio.tns` and `Vol 01.epub.folio.tns`. Running a batch again rebuilds the books at their output paths.

Use `pack.py` to combine a custom folder layout into one book, select chapters, or choose a prose font size. Run these examples from the project root. On Windows use `.\.venv\Scripts\python.exe` for the interpreter.

```sh
./.venv/bin/python pack.py "My Manga Volume"
./.venv/bin/python pack.py "Novel.epub" --font-size 18
./.venv/bin/python pack.py "Atlas.pdf" --compression smallest --chapters 3-7
./.venv/bin/python pack.py "Volume 01.folio.tns" --compression smallest
```

## Build and tests

The ready-to-transfer launcher is **folio/Folio.tns**. To rebuild, put the Ndless SDK and its ARM compiler on PATH and run **make**. The release used ARM GNU Toolchain 13.3.Rel1 and Ndless SDK revision `b88e4af2f74f10f3b28c9f7290a79de8fd9108b8`. The SDK build used its matching nspire-io library and included `<sys/syslimits.h>` in libsyscalls/realpath.c and libsyscalls/stdlib.cpp for this newlib. Dependency notices are in **third_party** and **fonts**.

```sh
make host
./.venv/bin/python tests/verify.py
./.venv/bin/python tests/verify_spreads.py
./.venv/bin/python tests/verify_navigation.py
./.venv/bin/python tests/verify_reader.py
./.venv/bin/python tests/verify_trim.py
./.venv/bin/python tests/verify_appearance.py
./.venv/bin/python tests/verify_batch.py
./.venv/bin/python tests/verify_posix_launcher.py
```

On Windows, run `.\.venv\Scripts\python.exe tests\verify_windows_batch.py` to check the drag-and-drop launchers with recursive folders, separate manga volumes, overlapping inputs, prepared books and mixed-success batches.

With the native build present, install **unicorn** and **pyelftools** and run **./.venv/bin/python tests/arm_smoke.py**. It runs the packaged ARM code with emulated keypad registers, touchpad reports and ticking or stalled clocks. It checks the centre boundary, delayed and missing coordinates, single and double clicks, switch bounce, and Menu from both bookmarks and chapters. It also exercises folder navigation, PDF links and cold resume. Run **./.venv/bin/python tests/arm_appearance.py** to check zoomed spreads and menu cursor movement against the packaged ARM code, including filesystem reads during hover. The release was tested in emulation. Touchpad feel on a physical CX II CAS still needs a hardware check.
