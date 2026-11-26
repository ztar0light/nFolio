"""Internal PDF destinations in the same coordinates as Folio's rendered pages."""
import ctypes as C
import math
import struct
from collections import defaultdict

HEADER = struct.Struct('<4sII')
MARK = struct.Struct('<IHH')
LINK = struct.Struct('<IIIHHIHH')
FOOTER = struct.Struct('<I4s')
LABEL_BYTES = 660


def get_destination(raw, doc, dest, action):
    if not dest and action and raw.FPDFAction_GetType(action) == raw.PDFACTION_GOTO:
        dest = raw.FPDFAction_GetDest(doc, action)
    if not dest:
        return None
    index = raw.FPDFDest_GetDestPageIndex(doc, dest)
    if not 0 <= index < len(doc):
        return None
    count, values = C.c_ulong(), (C.c_float * 4)()
    mode = raw.FPDFDest_GetView(dest, count, values)
    x = y = None
    if mode == raw.PDFDEST_VIEW_XYZ:
        has_x, has_y, has_z = C.c_int(), C.c_int(), C.c_int()
        left, top, zoom = C.c_float(), C.c_float(), C.c_float()
        if raw.FPDFDest_GetLocationInPage(dest, has_x, has_y, has_z, left, top, zoom):
            x = left.value if has_x.value else None
            y = top.value if has_y.value else None
    elif mode in (raw.PDFDEST_VIEW_FITH, raw.PDFDEST_VIEW_FITBH) and count.value:
        y = values[0]
    elif mode in (raw.PDFDEST_VIEW_FITV, raw.PDFDEST_VIEW_FITBV) and count.value:
        x = values[0]
    elif mode == raw.PDFDEST_VIEW_FITR and count.value == 4:
        x, y = values[0], values[3]
    return (index, x if x is not None and math.isfinite(x) else None,
            y if y is not None and math.isfinite(y) else None)


def outline(raw, doc):
    stack = [(raw.FPDFBookmark_GetFirstChild(doc, None), 0)]
    seen = set()
    while stack:
        item, depth = stack.pop()
        if not item:
            continue
        addr = C.cast(item, C.c_void_p).value
        if addr in seen:
            continue
        seen.add(addr)
        if len(seen) > 65536:
            raise ValueError('PDF outline is too large.')
        stack.append((raw.FPDFBookmark_GetNextSibling(doc, item), depth))
        stack.append((raw.FPDFBookmark_GetFirstChild(doc, item), min(depth + 1, 63)))
        target = get_destination(raw, doc, raw.FPDFBookmark_GetDest(doc, item), raw.FPDFBookmark_GetAction(item))
        if target is None:
            continue
        size = raw.FPDFBookmark_GetTitle(item, None, 0)
        if not 2 <= size <= 131072:
            continue
        buf = C.create_string_buffer(size)
        raw.FPDFBookmark_GetTitle(item, buf, size)
        yield (depth, buf.raw.decode('utf-16-le', 'replace').rstrip('\0'), target)


def pixel(raw, page, w, h, x, y):
    px, py = C.c_int(), C.c_int()
    if not raw.FPDF_PageToDevice(page, 0, 0, w, h, 0, x, y, px, py):
        raise ValueError('Could not map a PDF coordinate.')
    return px.value, py.value


def import_pdf(book, path, width, make_label):
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError:
        raise ValueError('Install pypdfium2 to prepare a PDF.') from None
    with pdfium.PdfDocument(path) as doc:
        marks = list(outline(raw, doc))
        starts = {0: path.stem}
        for depth, title, (page, _, _) in marks:
            if depth == 0:
                starts.setdefault(page, title)
                if page == 0:
                    starts[0] = title
        included, links = {}, []
        for index in range(len(doc)):
            if index in starts:
                book.chapter(starts[index])
            if not book.in_range:
                continue
            page = doc[index]
            try:
                top = book.height
                bmp = page.render(scale=width * book.streams[-1].scale / page.get_width())
                try:
                    image = bmp.to_pil().convert('RGB')
                    book.image(image)
                    if book.pdf_text is not None:
                        book.pdf_text.add_page(raw, page, image, book.height - top)
                finally:
                    bmp.close()
                h = book.height - top
                number = len(book.pages) - 1
                included[index] = (top, h)
                pos, link = C.c_int(), raw.FPDF_LINK()
                while raw.FPDFLink_Enumerate(page, pos, link):
                    dest = get_destination(raw, doc, raw.FPDFLink_GetDest(doc, link), raw.FPDFLink_GetAction(link))
                    rect = raw.FS_RECTF()
                    if dest is None or not raw.FPDFLink_GetAnnotRect(link, rect):
                        continue
                    if not all(math.isfinite(v) for v in (rect.left, rect.right, rect.top, rect.bottom)):
                        continue
                    a,b = pixel(raw,page,width,h,rect.left,rect.bottom)
                    c,d = pixel(raw,page,width,h,rect.right,rect.top)
                    x0,x1 = max(0,min(a,c)),min(width,max(a,c))
                    y0,y1 = max(0,min(b,d)),min(h,max(b,d))
                    if x0<x1 and y0<y1:
                        links.append(((number,top+y0,top+y1,x0,x1),dest))
            finally:
                page.close()
        targets = defaultdict(set)
        for target in [m[2] for m in marks] + [l[1] for l in links]:
            if target[0] in included:
                targets[target[0]].add(target)
        mapped = {}
        for index, group in targets.items():
            top,h = included[index]
            page = doc[index]
            try:
                left,upper = C.c_double(),C.c_double()
                if not raw.FPDF_DeviceToPage(page,0,0,width,h,0,0,0,left,upper):
                    raise ValueError('Could not map the PDF page origin.')
                for target in group:
                    _,x,y = target
                    px,py = pixel(raw,page,width,h,left.value if x is None else x,upper.value if y is None else y)
                    mapped[target] = (top + max(0,min(h-1,py)),max(0,min(width-1,px)))
            finally:
                page.close()
        records = [MARK.pack(*mapped[t],depth)+make_label(title,book.label_font) for depth,title,t in marks if t in mapped]
        boxes = [LINK.pack(*area,*mapped[target],0) for area,target in links if target in mapped]
        if len(records)>8192 or len(boxes)>100000:
            raise ValueError('Too many PDF destinations. Pack a chapter range.')
        if records or boxes:
            book.pdf_nav = HEADER.pack(b'NAV3',len(records),len(boxes))+b''.join(records+boxes)


def validate(data, starts, height):
    if not data:
        return
    if len(data)<HEADER.size:
        raise ValueError('Incomplete PDF navigation.')
    tag, nm, nl = HEADER.unpack_from(data)
    offset = HEADER.size + nm*(MARK.size+LABEL_BYTES)
    if tag!=b'NAV3' or nm>8192 or nl>100000 or len(data)!=offset+nl*LINK.size:
        raise ValueError('Invalid PDF navigation size.')
    for i in range(nm):
        y,x,depth=MARK.unpack_from(data,HEADER.size+i*(MARK.size+LABEL_BYTES))
        if y>=height or x>=320 or depth>63:
            raise ValueError('Invalid PDF bookmark destination.')
    previous=-1
    for page,y0,y1,x0,x1,dy,dx,unused in LINK.iter_unpack(memoryview(data)[offset:]):
        if not (previous<=page<len(starts) and starts[page]<=y0<y1<=(starts[page+1] if page+1<len(starts) else height)
                and 0<=x0<x1<=320 and dy<height and dx<320 and unused==0):
            raise ValueError('Invalid PDF link.')
        previous=page
