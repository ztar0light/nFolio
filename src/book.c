#include "folio.h"
#include "codec.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* A lightly trimmed 640-wide source can show up to nine strips at once. */
#define FIT_SLOTS 5
#define DETAIL_SLOTS 9
#define MAX_BLOCK 131072u
#define HEADER 56u
#define CHAPTER_SIZE (8u + F_LABEL_BYTES)

struct Book {
    FILE *file;
    BookTitle title;
    uint32_t height, nc, np, levels, chapter_pos, index[2], begin[2], end[2];
    Chapter *chapters;
    uint32_t *pages;
    /* 0/1 is a normal page's parity since the last prejoined spread, 2 a wide page. */
    uint8_t *page_layout;
    bool v2, v3, v4, master, tried_trim_cache, extended_cache;
    uint32_t marks, links, mark_pos, link_pos;
    uint16_t *master_strip;
    uint32_t tag[DETAIL_SLOTS], decodes, atlas_pos, atlas_bytes;
    uint32_t label_index;
    uint8_t current_label[F_LABEL_BYTES];
    unsigned cached_level;
    uint16_t *cache;
    uint8_t *encoded;
    Decoder *decoder;
};

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static bool read_at(FILE *f, uint32_t at, void *out, size_t bytes) {
    return fseek(f, (long)at, SEEK_SET) == 0 && fread(out, 1, bytes, f) == bytes;
}
static bool word(FILE *f, uint32_t at, uint32_t *out) {
    uint8_t v[4];
    if (!read_at(f, at, v, 4)) return false;
    *out = le32(v);
    return true;
}
static bool header(FILE *f, uint8_t *h, BookTitle *t) {
    if (!read_at(f,0,h,HEADER)) return false;
    bool v2=!memcmp(h,"FOLIO",5) && (h[5]>='2' && h[5]<='4') && h[6]<=2 && h[7]=='\n';
    if ((!v2 && memcmp(h,"FOLIO1\r\n",8)) || fread(t->label,1,F_LABEL_BYTES,f)!=F_LABEL_BYTES) return false;
    t->pages=le32(h+32);
    memcpy(t->id, h + 8, 16);
    return true;
}
bool book_peek(const char *path, BookTitle *t) {
    FILE *f = fopen(path,"rb");
    if (!f) return false;
    uint8_t h[HEADER];
    bool ok = header(f, h, t);
    fclose(f);
    return ok;
}
bool book_saved_page(const char *path,View v,uint32_t *page) {
    FILE *f=fopen(path,"rb");
    if (!f) return false;
    uint8_t h[HEADER]; bool ok=read_at(f,0,h,HEADER);
    if (ok) {
        uint32_t lo=0,hi=le32(h+32),at=le32(h+44),y=v.y>>(v.level?1:0);
        ok=hi>0 && hi<=500000 && at<=0x7fffffffu-hi*4;
        while (ok && hi-lo>1) {
            uint32_t m=lo+(hi-lo)/2,n;
            ok=word(f,at+m*4,&n);
            if (ok && (n&0x7fffffffu)<=y) lo=m; else hi=m;
        }
        *page=lo+1;
    }
    fclose(f); return ok;
}
/* Each trailer ends with its byte length and a four-byte tag. */
static bool chunk_before(FILE *f,uint32_t *end,const char *tag,uint32_t *length) {
    uint8_t footer[8];
    if (*end<8 || !read_at(f,*end-8,footer,8) || memcmp(footer+4,tag,4)) return false;
    *length=le32(footer);
    if (*length>*end-8) return false;
    *end-=8+*length; return true;
}
bool book_thumbnail(const char *path,uint16_t *pixels) {
    FILE *f=fopen(path,"rb");
    if (!f) return false;
    uint8_t h[8]; bool ok=false;
    if (read_at(f,0,h,8) && !memcmp(h,"FOLIO4",6) && fseek(f,0,SEEK_END)==0) {
        long n=ftell(f);
        if (n>=16 && (unsigned long)n<=0x7fffffffu) {
            uint32_t at=(uint32_t)n,length;
            if (chunk_before(f,&at,"FNAV",&length) && chunk_before(f,&at,"CVR4",&length) && length && length<=32768) {
                uint8_t *data=malloc(length);
                ok=data && read_at(f,at,data,length) && decode_cover(data,length,pixels);
                free(data);
            }
        }
    }
    fclose(f); return ok;
}
#define MARK_BYTES (8u+F_LABEL_BYTES)
#define LINK_BYTES 24u
static bool trailer(Book *b,uint32_t *size) {
    uint8_t head[12]; uint32_t len;
    if (!chunk_before(b->file,size,"FNAV",&len)) return false;
    uint32_t nav_at=*size;
    if (b->v4) {
        uint32_t cover;
        if (!chunk_before(b->file,size,"CVR4",&cover) || cover>32768) return false;
    }
    if (!len) return true;
    if (len<12 || !read_at(b->file,nav_at,head,12) || memcmp(head,"NAV3",4)) return false;
    b->marks=le32(head+4); b->links=le32(head+8);
    if (b->marks>8192 || b->links>100000 || 12+b->marks*MARK_BYTES+b->links*LINK_BYTES!=len) return false;
    b->mark_pos=nav_at+12; b->link_pos=b->mark_pos+b->marks*MARK_BYTES;
    return true;
}
static uint32_t strips(const Book *b, unsigned level) {
    return ((b->height << level) + F_BAND - 1) / F_BAND;
}
static bool indices(Book *b, uint32_t page_pos, uint32_t size) {
    uint32_t next = page_pos + b->np * 4;
    if (page_pos != b->chapter_pos + b->nc * CHAPTER_SIZE) return false;
    if (!b->master && b->levels == 1 && b->index[1]) return false;
    for (unsigned l = b->master?1:0; l < b->levels; l++) {
        if (b->index[l] != next) return false;
        next += (strips(b,l) + 1) * 4;
    }
    if (next >= size) return false;
    for (unsigned l = b->master?1:0; l < b->levels; l++) {
        if (!word(b->file,b->index[l],&b->begin[l])) return false;
        if (l==(b->master?1u:0u)) {
            if (b->begin[l]<next || b->begin[l]-next>800000) return false;
            b->atlas_pos=next; b->atlas_bytes=b->begin[l]-next; next=b->begin[l];
        }
        if (b->begin[l] != next ||
            !word(b->file,b->index[l] + 4 * strips(b,l),&b->end[l]) ||
            b->end[l] <= next || b->end[l] > size) return false;
        next = b->end[l];
    }
    return next == size;
}
static bool navigation(Book *b, uint32_t page_pos) {
    uint8_t c[8];
    for (uint32_t i = 0; i < b->nc; i++) {
        if (!read_at(b->file, b->chapter_pos + i * CHAPTER_SIZE, c, sizeof c)) return false;
        Chapter *ch = &b->chapters[i];
        ch->y = le32(c); ch->first_page = le32(c+4);
        if (ch->y >= b->height || ch->first_page >= b->np) return false;
        if (i && (ch->y <= b->chapters[i-1].y || ch->first_page <= b->chapters[i-1].first_page)) return false;
    }
    /* Read sequentially in blocks, avoiding a flash seek for each page. */
    if (fseek(b->file, (long)page_pos, SEEK_SET)) return false;
    uint8_t buf[512], parity=0;
    for (uint32_t i = 0; i < b->np;) {
        uint32_t n = b->np - i < 128 ? b->np-i : 128;
        if (fread(buf, 4, n, b->file) != n) return false;
        for (uint32_t j = 0; j < n; j++, i++) {
            uint32_t value=le32(buf+j*4);
            bool wide=b->v2 && (value&0x80000000u);
            b->pages[i]=wide ? value&0x7fffffffu : value;
            b->page_layout[i]=wide ? 2 : parity;
            parity=wide ? 0 : (parity^1);
            if (b->pages[i] >= b->height || (i && b->pages[i] <= b->pages[i-1])) return false;
        }
    }
    if (!b->v2 && !b->atlas_bytes) {
        /* Image-only 0.1 books already record each page height at width 320. */
        parity=0;
        for (uint32_t i=0;i<b->np;i++) {
            uint32_t end=i+1<b->np ? b->pages[i+1] : b->height;
            bool wide=end-b->pages[i]<F_WIDTH;
            b->page_layout[i]=wide?2:parity; parity=wide?0:(parity^1);
        }
    }
    if (b->chapters[0].y || b->chapters[0].first_page || b->pages[0]) return false;
    for (uint32_t i=0; i<b->nc; i++) if (b->pages[b->chapters[i].first_page] != b->chapters[i].y) return false;
    return true;
}
Book *book_open(const char *path, const char **err) {
    *err = "There is too little free memory to open this book.";
    Book *b = calloc(1,sizeof(*b));
    if (!b) return NULL;
    b->file = fopen(path, "rb");
    if (!b->file) { *err = "Could not open the book file."; goto fail; }
    uint8_t h[HEADER];
    *err = "Incomplete or incompatible book. Transfer the pack again.";
    if (!header(b->file,h,&b->title) || fseek(b->file, 0, SEEK_END)) goto fail;
    long file_size = ftell(b->file);
    if (file_size <= 0 || (unsigned long)file_size > 0x7fffffffUL) goto fail;
    b->v4=h[5]=='4'; b->v3=h[5]>='3'; b->v2=b->v3 || h[5]=='2';
    uint32_t core=(uint32_t)file_size;
    if (b->v3 && !trailer(b,&core)) goto fail;
    b->height = le32(h+24); b->nc = le32(h+28); b->np = le32(h+32); b->levels = le32(h+36);
    b->chapter_pos = le32(h+40); uint32_t page_pos = le32(h+44);
    b->index[0] = le32(h+48); b->index[1] = le32(h+52);
    if (b->v3 && b->levels==0x102) {
        if (b->index[1]) goto fail;
        b->master=true; b->levels=2; b->index[1]=b->index[0]; b->index[0]=0;
    }
    if (!b->height || b->height > 0x3fffffffu || !b->nc || b->nc > 8192 || !b->np || b->np > 500000 ||
        (b->levels != 1 && b->levels != 2) || b->chapter_pos != HEADER + F_LABEL_BYTES || !indices(b,page_pos,core)) goto fail;
    *err = "There is too little free memory to open this book.";
    b->chapters = malloc(b->nc * sizeof(Chapter)); b->pages = malloc(b->np * sizeof(uint32_t));
    b->page_layout=malloc(b->np);
    b->decoder = decoder_new(); b->encoded = malloc(MAX_BLOCK);
    b->cache=malloc(FIT_SLOTS*F_BAND*F_WIDTH*b->levels*sizeof(uint16_t));
    if (b->master && !(b->master_strip=malloc(2*F_WIDTH*F_BAND*sizeof(uint16_t)))) goto fail;
    if (!b->chapters || !b->pages || !b->page_layout || !b->decoder || !b->encoded || !b->cache) goto fail;
    *err = "Damaged chapter or page index. Transfer the pack again.";
    if (!navigation(b,page_pos)) goto fail;
    if (b->atlas_bytes) {
        uint8_t *data=malloc(b->atlas_bytes);
        if (!data) { *err="Too little memory for the book font."; goto fail; }
        bool ok=read_at(b->file,b->atlas_pos,data,b->atlas_bytes) && decoder_load_atlas(b->decoder,data,b->atlas_bytes);
        free(data);
        if (!ok) { *err="Could not load the book font. Transfer the pack again."; goto fail; }
    }
    b->cached_level = 2; b->label_index=UINT32_MAX;
    *err = NULL;
    return b;
fail:
    book_close(b);
    return NULL;
}
void book_close(Book *b) {
    if (!b) return;
    if (b->file) fclose(b->file);
    free(b->master_strip); free(b->page_layout); free(b->pages); free(b->chapters); free(b->cache); free(b->encoded); decoder_free(b->decoder); free(b);
}
const BookTitle *book_title(const Book *b) { return &b->title; }
uint32_t book_chapters(const Book *b) { return b->nc; }
uint32_t book_page_count(const Book *b) { return b->np; }
unsigned book_levels(const Book *b) { return b->levels; }
uint32_t book_decodes(const Book *b) { return b->decodes; }
Chapter book_chapter(const Book *b, uint32_t i) { return b->chapters[i]; }
uint32_t book_page(const Book *b, uint32_t i) { return b->pages[i]; }
bool book_label(Book *b, uint32_t i, uint8_t *label) {
    if (i>=b->nc) return false;
    if (b->label_index!=i) {
        b->label_index=UINT32_MAX;
        if (!read_at(b->file,b->chapter_pos+i*CHAPTER_SIZE+8,b->current_label,F_LABEL_BYTES)) return false;
        b->label_index=i;
    }
    memcpy(label,b->current_label,F_LABEL_BYTES);
    return true;
}
static uint32_t locate(const Book *b, uint32_t y, bool chapter) {
    uint32_t lo = 0, hi = chapter ? b->nc : b->np;
    while (hi - lo > 1) {
        uint32_t m = lo + (hi - lo)/2;
        if ((chapter ? b->chapters[m].y : b->pages[m]) <= y) lo = m; else hi = m;
    }
    return lo;
}
uint32_t chapter_at(const Book *b, View v) { return locate(b, v.y >> v.level, true); }
uint32_t page_at(const Book *b, View v) { return locate(b, v.y >> v.level, false); }
static unsigned visible_rows(View v) {
    if (v.level) return 4*F_HEIGHT/3;
    unsigned scale=F_TRIM_UNIT+v.trim;
    return (F_HEIGHT*F_TRIM_UNIT+scale-1)/scale;
}
static uint32_t page_height(const Book *b,uint32_t i) {
    return (i+1<b->np ? b->pages[i+1] : b->height)-b->pages[i];
}
static uint32_t page_start(const Book *b,uint32_t page,View v) {
    uint32_t top=b->pages[page]<<v.level;
    if (!v.trim || v.level) return top;
    unsigned centre=page_height(b,page), half=visible_rows(v)/2;
    if (centre>F_HEIGHT/2) centre=F_HEIGHT/2;
    return centre>half ? top+centre-half : top;
}
void view_normalize(const Book *b, View *v) {
    if (v->level>1) v->level=0;
    if (v->trim>F_TRIM_MAX) v->trim=F_TRIM_MAX;
    uint32_t max_x = v->level ? 2*F_WIDTH-(4*F_WIDTH+2)/3 : 0;
    uint32_t height = b->height << v->level;
    uint32_t visible = visible_rows(*v);
    uint32_t limit = height > visible ? height - visible : 0;
    uint32_t last=page_start(b,b->np-1,*v);
    /* Even a short final source page can be selected at its top. */
    if (last > limit) limit = last;
    if (v->x > max_x) v->x = max_x;
    if (v->y > limit) v->y = limit;
    v->hud = !!v->hud;
    v->spread &= SPREAD_MASK;
    if (!(v->spread&SPREAD_ON)) v->spread&=~SPREAD_DETAIL;
    if (!(v->spread&SPREAD_DETAIL)) v->spread_x=v->spread_y=0;
}
static uint32_t shifted(uint32_t n, int delta) {
    if (delta < 0) return (uint32_t)(-delta) > n ? 0 : n - (uint32_t)(-delta);
    return n + (uint32_t)delta;
}
static int trim_distance(View v,int pixels) {
    if (v.level || !v.trim) return pixels;
    int scale=(int)(F_TRIM_UNIT+v.trim);
    return (pixels*(int)F_TRIM_UNIT+(pixels<0?-scale/2:scale/2))/scale;
}
void view_scroll(const Book *b, View *v, int pixels) {
    v->y=shifted(v->y,trim_distance(*v,pixels));
    view_normalize(b,v);
}
static void clamp_page(const Book *b,View *v,uint32_t page) {
    uint32_t top=b->pages[page]<<v->level, height=page_height(b,page)<<v->level;
    unsigned visible=visible_rows(*v);
    uint32_t bottom=height>visible ? top+height-visible : top;
    /* Prose positions can be shorter than the screen. Allow the same
       centre crop that the reader saw at the top of the untrimmed view. */
    uint32_t start=page_start(b,page,*v);
    if (bottom<start) bottom=start;
    if (v->y<top) v->y=top;
    if (v->y>bottom) v->y=bottom;
}
void view_nudge(const Book *b,View *v,int pixels) {
    uint32_t page=page_at(b,*v);
    view_scroll(b,v,v->level?pixels*4/3:pixels);
    if (!v->continuous) clamp_page(b,v,page);
}
void view_pan(const Book *b, View *v, int p) { v->x = shifted(v->x,p); view_normalize(b,v); }
static uint32_t centre_y(View v) {
    return (v.y+visible_rows(v)/2)>>v.level;
}
static void anchor_centre(const Book *b,View *v,uint32_t centre,uint32_t page) {
    v->y=shifted(centre<<v->level,-(int)(visible_rows(*v)/2));
    if (!v->continuous || (v->spread&SPREAD_ON)) clamp_page(b,v,page);
    view_normalize(b,v);
}
void view_trim(const Book *b,View *v,int direction) {
    if (v->spread&SPREAD_ON) return;
    unsigned trim=v->trim;
    if (direction>0 && trim<F_TRIM_MAX) trim++;
    if (direction<0 && trim) trim--;
    bool detail=v->level && !(v->spread&SPREAD_ON);
    if (trim==v->trim && !detail) return;
    uint32_t centre=centre_y(*v), page=page_at(b,*v);
    v->trim=trim;
    if (detail) v->level=0;
    if (!v->level) { v->x=0; anchor_centre(b,v,centre,page); }
}
void view_zoom(const Book *b, View *v) {
    if (v->trim) {
        uint32_t centre=centre_y(*v), page=page_at(b,*v);
        v->level=!v->level;
        v->x=v->level ? (2*F_WIDTH-(4*F_WIDTH+2)/3)/2 : 0;
        anchor_centre(b,v,centre,page);
        return;
    }
    if (v->level) { v->level=0; v->y/=2; v->x=0; }
    else { v->level=1; v->y*=2; v->x=0; }
    view_normalize(b,v);
}
void view_turn(const Book *b, View *v, int direction) {
    uint32_t i = page_at(b,*v);
    if (direction > 0) {
        if (i + 1 < b->np) i++;
        else { v->y = UINT32_MAX; view_normalize(b,v); return; }
    } else if (v->y<=page_start(b,i,*v) && i) i--;
    v->y=page_start(b,i,*v); v->x=0; view_normalize(b,v);
}
void view_chapter(const Book *b, View *v, uint32_t i) {
    if (i >= b->nc) return;
    v->y=page_start(b,b->chapters[i].first_page,*v); v->x=0; view_normalize(b,v);
}
static Spread spread_at(const Book *b, uint32_t page, unsigned settings) {
    Spread pair={page,page};
    bool all=!!(settings&SPREAD_PAIR_WIDE);
    if (!all && b->page_layout[page]==2) return pair;
    unsigned first_parity=!!(settings&SPREAD_SHIFT);
    if ((all?(page&1u):b->page_layout[page])==first_parity) {
        if (page+1<b->np && (all || b->page_layout[page+1]!=2)) pair.last++;
    } else if (page && (all || b->page_layout[page-1]!=2)) pair.first--;
    return pair;
}
Spread book_spread(const Book *b, View v) { return spread_at(b,page_at(b,v),v.spread); }
void view_toggle_spread(const Book *b, View *v) {
    if (!(v->spread&SPREAD_ALIGNED)) {
        /* The first press joins the page under the cursor to the following page. */
        if (b->page_layout[page_at(b,*v)]==1) v->spread|=SPREAD_SHIFT;
        v->spread|=SPREAD_ALIGNED;
    }
    v->spread^=SPREAD_ON;
    v->spread&=~SPREAD_DETAIL;
    v->spread_x=v->spread_y=0;
}
void view_turn_spread(const Book *b, View *v,int direction) {
    Spread now=book_spread(b,*v);
    if ((direction>0 && now.last+1==b->np) || (direction<0 && !now.first)) return;
    uint32_t target=direction>0 ? now.last+1 : now.first-1;
    Spread next=spread_at(b,target,v->spread);
    v->y=page_start(b,next.first,*v); v->x=0;
    v->spread_x=v->spread_y=0;
    view_normalize(b,v);
}
static size_t load_block(Book *b, unsigned level, uint32_t n) {
    uint8_t pair[8];
    if (!read_at(b->file,b->index[level]+n*4,pair,8)) return 0;
    uint32_t from=le32(pair), to=le32(pair+4);
    if (to<=from || from<b->begin[level] || to>b->end[level] || to-from>MAX_BLOCK) return 0;
    return read_at(b->file,from,b->encoded,to-from) ? to-from : 0;
}
static uint16_t mean565(uint16_t a,uint16_t b);
static bool reduce_master(Book *b,uint32_t n,uint16_t *pixels) {
    for (unsigned half=0;half<2;half++) {
        uint32_t strip=n*2+half;
        uint16_t *out=pixels+half*(F_BAND/2)*F_WIDTH;
        if (strip>=strips(b,1)) { memset(out,0xff,F_BAND*F_WIDTH); continue; }
        size_t len=load_block(b,1,strip);
        if (len==5 && b->encoded[0]==7) {
            uint32_t ref=le32(b->encoded+1);
            if (ref>=strip) return false;
            len=load_block(b,1,ref);
        }
        if (len && b->encoded[0]==10) {
            if (!decode_half(b->decoder,b->encoded+1,len-1,out)) return false;
            continue;
        }
        if (!len || !decode_band(b->decoder,b->encoded,len,2*F_WIDTH,b->master_strip)) return false;
        for (unsigned y=0;y<F_BAND;y+=2) {
            const uint16_t *a=b->master_strip+y*2*F_WIDTH,*z=a+2*F_WIDTH;
            for (unsigned x=0;x<2*F_WIDTH;x+=2)
                *out++=mean565(mean565(a[x],a[x+1]),mean565(z[x],z[x+1]));
        }
    }
    return true;
}
static uint16_t *band(Book *b, unsigned level, uint32_t n) {
    uint32_t slot=b->extended_cache?n%DETAIL_SLOTS:n%FIT_SLOTS;
    unsigned width = F_WIDTH << level;
    uint16_t *pixels = b->cache + slot * F_BAND * width;
    if (b->tag[slot] == n) return pixels;
    if (b->master && level==0) {
        b->tag[slot]=UINT32_MAX;
        if (!reduce_master(b,n,pixels)) return NULL;
        b->tag[slot]=n; b->decodes++; return pixels;
    }
    size_t length=load_block(b,level,n);
    if (!length) return NULL;
    if (b->encoded[0]==7) {
        if (!b->v2 || length!=5) return NULL;
        uint32_t ref=le32(b->encoded+1);
        if (ref>=n) return NULL;
        unsigned other=b->extended_cache?ref%DETAIL_SLOTS:ref%FIT_SLOTS;
        if (b->tag[other]==ref) {
            if (other!=slot) memcpy(pixels,b->cache+other*F_BAND*width,F_BAND*width*2);
            b->tag[slot]=n;
            return pixels;
        }
        length=load_block(b,level,ref);
        if (!length || b->encoded[0]==7) return NULL;
    }
    b->tag[slot]=UINT32_MAX;
    if (!decode_band(b->decoder,b->encoded,length,width,pixels)) return NULL;
    b->tag[slot] = n; b->decodes++;
    return pixels;
}
static uint16_t mean565(uint16_t a,uint16_t b) {
    return (uint16_t)((a&b)+(((a^b)&0xf7deu)>>1));
}
static void select_level(Book *b,unsigned level) {
    if (b->cached_level!=level) {
        memset(b->tag,0xff,sizeof(b->tag)); b->cached_level=level;
    }
}
/* Filter four samples per display pixel. Typical 320-to-160 manga pages use
   the exact 2x2 footprint, which keeps one-pixel lettering and fold lines. */
static bool draw_leaf(Book *b,uint32_t page,int left,int top,unsigned w,unsigned h,uint16_t *frame,unsigned stride,unsigned screen_h,uint32_t height) {
    int x0screen=left<0?0:left, y0screen=top<0?0:top;
    int right=left+(int)w, bottom=top+(int)h;
    if (right>(int)stride) right=(int)stride;
    if (bottom>(int)screen_h) bottom=(int)screen_h;
    if (right<=x0screen || bottom<=y0screen) return true;
    unsigned columns=(unsigned)(right-x0screen);
    unsigned level=b->cached_level, width=F_WIDTH<<level;
    uint16_t x0[F_WIDTH], x1[F_WIDTH];
    for (unsigned i=0;i<columns;i++) {
        unsigned x=i+(unsigned)(x0screen-left);
        x0[i]=(uint16_t)(((4*x+1)*width)/(4*w));
        x1[i]=(uint16_t)(((4*x+3)*width)/(4*w));
    }
    uint32_t start=b->pages[page]<<level;
    height<<=level;
    for (int row=y0screen;row<bottom;row++) {
        unsigned y=(unsigned)(row-top);
        uint32_t rows[2];
        rows[0]=start+(uint32_t)(((uint64_t)(4*y+1)*height)/(4*h));
        rows[1]=start+(uint32_t)(((uint64_t)(4*y+3)*height)/(4*h));
        uint16_t *out=frame+(unsigned)row*stride+x0screen;
        for (unsigned pass=0;pass<2;pass++) {
            if (pass && rows[0]==rows[1]) break;
            const uint16_t *src=band(b,level,rows[pass]/F_BAND);
            if (!src) return false;
            src+=(rows[pass]%F_BAND)*width;
            for (unsigned x=0;x<columns;x++) {
                uint16_t sample=mean565(src[x0[x]],src[x1[x]]);
                out[x]=pass ? mean565(out[x],sample) : sample;
            }
        }
    }
    return true;
}
static unsigned scaled(uint32_t length,unsigned numerator,uint32_t denominator) {
    unsigned n=(unsigned)(((uint64_t)length*numerator+denominator/2)/denominator);
    return n ? n : 1;
}
typedef struct { uint32_t page; int x,y; unsigned w,h; } Leaf;
static int spread_edge(int edge,int centre) {
    int d=edge-centre, extra=(abs(d)+1)/2;
    return edge+(d<0?-extra:extra);
}
static unsigned spread_layout(const Book *b,View v,Leaf leaf[2]) {
    Spread p=book_spread(b,v);
    bool two=p.first!=p.last;
    uint32_t a=page_height(b,p.first), z=page_height(b,p.last), tallest=a>z?a:z;
    unsigned num=two ? F_WIDTH/2 : F_WIDTH;
    uint32_t den=F_WIDTH;
    if ((uint64_t)tallest*num>F_HEIGHT*F_WIDTH) { num=F_HEIGHT; den=tallest; }
    unsigned w=scaled(F_WIDTH,num,den), h=scaled(tallest,num,den);
    int left=(F_WIDTH-(int)w*(two?2:1))/2, top=(F_HEIGHT-(int)h)/2;
    uint32_t first=(v.spread&SPREAD_LTR)?p.first:p.last;
    uint32_t second=first==p.first?p.last:p.first;
    leaf[0]=(Leaf){first,left,top,w,scaled(page_height(b,first),num,den)};
    leaf[1]=(Leaf){second,left+(int)w,top,w,scaled(page_height(b,second),num,den)};
    unsigned count=two?2:1;
    if (v.spread&SPREAD_DETAIL) for (unsigned i=0;i<count;i++) {
        Leaf *f=&leaf[i];
        int right=spread_edge(f->x+(int)f->w,F_WIDTH/2);
        int bottom=spread_edge(f->y+(int)f->h,F_HEIGHT/2);
        f->x=spread_edge(f->x,F_WIDTH/2); f->y=spread_edge(f->y,F_HEIGHT/2);
        f->w=(unsigned)(right-f->x); f->h=(unsigned)(bottom-f->y);
    }
    return count;
}
static int bounded_pan(int requested,int first,int end,int extent) {
    int lo=first<0?first:0, hi=end>extent?end-extent:0;
    return requested<lo?lo:requested>hi?hi:requested;
}
static void clip_spread_pan(View *v,const Leaf leaf[2],unsigned count) {
    const Leaf *right=&leaf[count-1];
    unsigned height=leaf[0].h>right->h?leaf[0].h:right->h;
    v->spread_x=bounded_pan(v->spread_x,leaf[0].x,right->x+(int)right->w,F_WIDTH);
    v->spread_y=bounded_pan(v->spread_y,leaf[0].y,leaf[0].y+(int)height,F_HEIGHT);
}
void view_pan_spread(const Book *b,View *v,int dx,int dy) {
    Leaf leaf[2]; unsigned n=spread_layout(b,*v,leaf);
    v->spread_x+=dx; v->spread_y+=dy;
    clip_spread_pan(v,leaf,n);
}
static unsigned leaves(const Book *b,View v,Leaf leaf[2]) {
    unsigned n=spread_layout(b,v,leaf);
    if (v.spread&SPREAD_DETAIL) {
        clip_spread_pan(&v,leaf,n);
        for (unsigned i=0;i<n;i++) { leaf[i].x-=v.spread_x; leaf[i].y-=v.spread_y; }
    }
    return n;
}
static bool render_spread(Book *b,View v,uint16_t *frame) {
    Leaf leaf[2]; unsigned count=leaves(b,v,leaf);
    /* A prejoined landscape can grow wider than the 320-pixel fit image. */
    select_level(b,b->levels==2 && leaf[0].w>F_WIDTH);
    memset(frame,0xff,F_WIDTH*F_HEIGHT*2);
    for (unsigned i=0;i<count;i++) {
        Leaf f=leaf[i]; if (!draw_leaf(b,f.page,f.x,f.y,f.w,f.h,frame,F_WIDTH,F_HEIGHT,page_height(b,f.page))) return false;
    }
    return true;
}
bool book_cover(Book *b,uint16_t *pixels,unsigned w,unsigned h) {
    if (!w || !h || w>F_WIDTH || h>F_HEIGHT) return false;
    uint32_t height=page_height(b,0);
    if (height>3*F_WIDTH) height=3*F_WIDTH/2;
    unsigned dw=w, dh=scaled(height,w,F_WIDTH);
    if (dh>h) { dh=h; dw=scaled(F_WIDTH,h,height); }
    for (unsigned i=0;i<w*h;i++) pixels[i]=0xf7bc;
    select_level(b,0);
    return draw_leaf(b,0,(int)(w-dw)/2,(int)(h-dh)/2,dw,dh,pixels,w,h,height);
}
static unsigned le16(const uint8_t *p) { return p[0]|((unsigned)p[1]<<8); }
unsigned book_bookmarks(const Book *b) { return b->marks; }
bool book_links(const Book *b) { return b->links!=0; }
bool book_mark(Book *b,unsigned i,Destination *dest,uint8_t *label) {
    uint8_t d[8]; uint32_t off=b->mark_pos+i*MARK_BYTES;
    if (i>=b->marks || !read_at(b->file,off,d,8)) return false;
    *dest=(Destination){le32(d),le16(d+4),le16(d+6)};
    return dest->y<b->height && dest->x<F_WIDTH && dest->depth<=63 &&
        (!label || fread(label,1,F_LABEL_BYTES,b->file)==F_LABEL_BYTES);
}
void view_destination(const Book *b,View *v,Destination d) {
    v->spread&=~(SPREAD_ON|SPREAD_DETAIL); v->y=d.y<<v->level; v->x=d.x<<v->level; view_normalize(b,v);
}
/* Source-space pixel centres in units of 1 / (2 * scale). Only the
   horizontal axis has a permanent inset. The vertical anchor scrolls. */
static unsigned trim_point(unsigned pixel,unsigned extent,unsigned steps) {
    return (2*pixel+1)*F_TRIM_UNIT+extent*steps;
}
bool book_hit(Book *b,View v,int x,int y,Destination *dest) {
    if (!b->links || (unsigned)x>=F_WIDTH || (unsigned)y>=F_HEIGHT) return false;
    uint32_t ay,ax;
    if (v.spread&SPREAD_ON) {
        Leaf l[2]; unsigned count=leaves(b,v,l),i;
        for (i=0;i<count;i++) if (x>=l[i].x && y>=l[i].y && (unsigned)(x-l[i].x)<l[i].w && (unsigned)(y-l[i].y)<l[i].h) break;
        if (i==count) return false;
        ax=(unsigned)(x-l[i].x)*F_WIDTH/l[i].w;
        ay=b->pages[l[i].page]+(uint32_t)((uint64_t)(y-l[i].y)*page_height(b,l[i].page)/l[i].h);
    } else if (v.level) {
        ax=(v.x+(unsigned)x*4/3)/2; ay=(v.y+(unsigned)y*4/3)/2;
    } else if (v.trim) {
        unsigned divisor=2*(F_TRIM_UNIT+v.trim);
        ax=trim_point((unsigned)x,F_WIDTH,v.trim)/divisor;
        ay=v.y+trim_point((unsigned)y,0,v.trim)/divisor;
    } else { ax=v.x+(unsigned)x; ay=v.y+(unsigned)y; }
    if (ax>=F_WIDTH || ay>=b->height) return false;
    uint32_t page=locate(b,ay,false),lo=0,hi=b->links;
    while (lo<hi) {
        uint32_t mid=lo+(hi-lo)/2,p;
        if (!word(b->file,b->link_pos+mid*LINK_BYTES,&p)) return false;
        if (p<page) lo=mid+1; else hi=mid;
    }
    if (fseek(b->file,(long)(b->link_pos+lo*LINK_BYTES),SEEK_SET)) return false;
    for (;lo<b->links;lo++) {
        uint8_t r[LINK_BYTES];
        if (fread(r,1,sizeof(r),b->file)!=sizeof(r) || le32(r)!=page) break;
        if (ay>=le32(r+4) && ay<le32(r+8) && ax>=le16(r+12) && ax<le16(r+14)) {
            *dest=(Destination){le32(r+16),le16(r+20),0};
            return dest->y<b->height && dest->x<F_WIDTH;
        }
    }
    return false;
}
/* A 1.5x screen pixel covers 4/3 of a pixel in a 640-wide master.
   Keep saved anchors in original 2x coordinates across reader upgrades. */
static uint16_t blend_43(uint16_t a,uint16_t b,unsigned phase) {
    uint16_t mid=mean565(a,b);
    return phase==1?mid:phase==0?mean565(a,mid):mean565(mid,b);
}
static bool render_zoom(Book *b,View v,unsigned level,uint16_t *frame) {
    unsigned width=F_WIDTH<<level, xs[F_WIDTH], phases[F_WIDTH];
    uint32_t total=b->height<<level;
    for (unsigned x=0;x<F_WIDTH;x++) {
        unsigned native=v.x+4*x/3;
        xs[x]=level?native:native/2; phases[x]=x%3;
    }
    for (unsigned row=0;row<F_HEIGHT;row++) {
        uint32_t native=v.y+4*row/3, sy=level?native:native/2;
        uint16_t *out=frame+row*F_WIDTH;
        if (sy>=total) { memset(out,0xff,F_WIDTH*2); continue; }
        const uint16_t *a=band(b,level,sy/F_BAND);
        if (!a) return false;
        a+=width*(sy%F_BAND);
        if (!level) { for (unsigned x=0;x<F_WIDTH;x++) out[x]=a[xs[x]]; continue; }
        uint32_t next=sy+1<total?sy+1:sy;
        const uint16_t *z=band(b,level,next/F_BAND);
        if (!z) return false;
        z+=width*(next%F_BAND);
        for (unsigned x=0;x<F_WIDTH;x++) {
            unsigned i=xs[x], j=i+1<width?i+1:i;
            out[x]=blend_43(blend_43(a[i],a[j],phases[x]),blend_43(z[i],z[j],phases[x]),row%3);
        }
    }
    return true;
}
typedef struct { unsigned index, weight; } Sample;
static Sample trim_sample(unsigned point,unsigned density,unsigned scale) {
    unsigned centre=point*density;
    if (centre<=scale) return (Sample){0,0};
    unsigned n=centre-scale, divisor=2*scale;
    return (Sample){n/divisor,((n%divisor)*32+scale)/divisor};
}
static uint16_t mix565(uint16_t a,uint16_t b,unsigned weight) {
    /* Keep green above red and blue so one multiply blends all three. */
    const uint32_t mask=0x07e0f81fu;
    uint32_t left=((uint32_t)a<<16|a)&mask, right=((uint32_t)b<<16|b)&mask;
    uint32_t mixed=(((left<<5)+(right-left)*weight)>>5)&mask;
    return (uint16_t)(mixed|(mixed>>16));
}
static bool render_trim(Book *b,View v,unsigned level,uint16_t *frame) {
    unsigned density=1u<<level, width=F_WIDTH*density, scale=F_TRIM_UNIT+v.trim;
    uint32_t top=v.y*density, height=b->height*density;
    if (level && !b->tried_trim_cache) {
        b->tried_trim_cache=true;
        uint16_t *larger=realloc(b->cache,DETAIL_SLOTS*F_BAND*width*sizeof(*larger));
        if (larger) {
            b->cache=larger; b->extended_cache=true;
            memset(b->tag,0xff,sizeof(b->tag));
        }
    }
    Sample xs[F_WIDTH];
    for (unsigned x=0;x<F_WIDTH;x++) xs[x]=trim_sample(trim_point(x,F_WIDTH,v.trim),density,scale);
    for (unsigned row=0;row<F_HEIGHT;row++) {
        Sample sample=trim_sample(trim_point(row,0,v.trim),density,scale);
        uint32_t y=top+sample.index;
        uint16_t *out=frame+row*F_WIDTH;
        if (y>=height) { memset(out,0xff,F_WIDTH*sizeof(*out)); continue; }
        const uint16_t *a=band(b,level,y/F_BAND);
        if (!a) return false;
        a+=(y%F_BAND)*width;
        if (density==2 && scale==2*F_TRIM_UNIT) {
            memcpy(out,a+F_WIDTH/2,F_WIDTH*sizeof(*out));
            continue;
        }
        if (!sample.weight || y+1==height) {
            for (unsigned x=0;x<F_WIDTH;x++) {
                unsigned i=xs[x].index;
                out[x]=mix565(a[i],a[i+1],xs[x].weight);
            }
        } else {
            const uint16_t *z=band(b,level,(y+1)/F_BAND);
            if (!z) return false;
            z+=((y+1)%F_BAND)*width;
            for (unsigned x=0;x<F_WIDTH;x++) {
                unsigned i=xs[x].index, weight=xs[x].weight;
                out[x]=mix565(mix565(a[i],a[i+1],weight),mix565(z[i],z[i+1],weight),sample.weight);
            }
        }
    }
    return true;
}
bool book_render(Book *b, View v, uint16_t *frame) {
    view_normalize(b,&v);
    if (v.spread&SPREAD_ON) return render_spread(b,v,frame);
    unsigned level=b->levels==2 && (v.level || v.trim);
    select_level(b,level);
    if (v.level) return render_zoom(b,v,level,frame);
    if (v.trim) return render_trim(b,v,level,frame);
    const uint32_t width = F_WIDTH << v.level, total = b->height << v.level;
    uint32_t y = v.y, drawn = 0;
    while (drawn < F_HEIGHT && y < total) {
        uint32_t offset = y % F_BAND, n = F_BAND - offset;
        if (n > F_HEIGHT - drawn) n = F_HEIGHT - drawn;
        if (n > total - y) n = total-y;
        const uint16_t *p = band(b,v.level,y/F_BAND);
        if (!p) return false;
        p += offset*width + v.x;
        uint16_t *out = frame + drawn*F_WIDTH;
        if (width == F_WIDTH) memcpy(out, p, n*F_WIDTH*2);
        else for (uint32_t i=0; i<n; i++) memcpy(out+i*F_WIDTH, p+i*width, F_WIDTH*2);
        drawn += n; y += n;
    }
    if (drawn < F_HEIGHT) memset(frame+drawn*F_WIDTH,0xff,(F_HEIGHT-drawn)*F_WIDTH*2);
    return true;
}
