#include "appearance.h"
#include <stdio.h>
#include <string.h>

#define COVER_PIXELS (F_COVER_W*F_COVER_H)
#define COVER_SLOTS 4
typedef struct {
    const Entry *book;
    uint32_t used;
    bool valid;
    uint16_t pixels[COVER_PIXELS];
} Cover;

static char shelf_root[F_PATH];
static Appearance prefs;
static uint32_t generation,uses;
static const Entry *last_book,*wall_book;
static Cover covers[COVER_SLOTS];
static uint16_t wallpaper[F_WIDTH*F_HEIGHT];
static bool wall_known,wall_ok,custom_missing;

/* Separate settings from per-book saves. Alternate slots so an interrupted
   write leaves the previous theme readable. */
static uint32_t get32(const uint8_t *b) {
    return b[0]|(uint32_t)b[1]<<8|(uint32_t)b[2]<<16|(uint32_t)b[3]<<24;
}
static void put32(uint8_t *b,uint32_t n) {
    for (unsigned i=0;i<4;i++) b[i]=(uint8_t)(n>>(8*i));
}
static bool style_path(char *path,unsigned slot) {
    return join_path(path,shelf_root,slot?"folio-style-b.tns":"folio-style-a.tns");
}
static bool read_style(unsigned slot,Appearance *style,uint32_t *seq) {
    char path[F_PATH]; uint8_t b[16];
    if (!style_path(path,slot)) return false;
    FILE *f=fopen(path,"rb");
    if (!f) return false;
    bool ok=fread(b,1,sizeof b,f)==sizeof b;
    fclose(f);
    if (!ok || memcmp(b,"FUI1",4) || get32(b+12)!=folio_checksum(b,12) ||
        b[8]>=BG_COUNT || b[9]>=THEME_COUNT || (b[4]&1u)!=slot) return false;
    *seq=get32(b+4); *style=(Appearance){(Background)b[8],(Theme)b[9]};
    return true;
}
void appearance_load(const char *root) {
    snprintf(shelf_root,sizeof shelf_root,"%s",root);
    memset(covers,0,sizeof covers); last_book=wall_book=NULL;
    wall_known=wall_ok=custom_missing=false; uses=generation=0; prefs=(Appearance){0};
    Appearance a,b; uint32_t sa=0,sb=0;
    bool have_a=read_style(0,&a,&sa), have_b=read_style(1,&b,&sb);
    if (have_b && (!have_a || (int32_t)(sb-sa)>0)) { generation=sb; prefs=b; }
    else if (have_a) { generation=sa; prefs=a; }
}
Appearance appearance_get(void) { return prefs; }
bool appearance_save(Appearance value) {
    if (value.background>=BG_COUNT || value.theme>=THEME_COUNT) return false;
    uint32_t seq=generation+1;
    char path[F_PATH]; uint8_t b[16]={'F','U','I','1'};
    if (!style_path(path,seq&1u)) return false;
    put32(b+4,seq); b[8]=(uint8_t)value.background; b[9]=(uint8_t)value.theme;
    put32(b+12,folio_checksum(b,12));
    FILE *f=fopen(path,"wb");
    if (!f) return false;
    bool ok=fwrite(b,1,sizeof b,f)==sizeof b && fflush(f)==0;
    if (fclose(f)!=0 || !ok) return false;
    if (value.background!=prefs.background || (value.theme==THEME_LIGHT)!=(prefs.theme==THEME_LIGHT)) {
        wall_known=false; custom_missing=false;
    }
    prefs=value; generation=seq; return true;
}
void appearance_reading(const Entry *entry) { last_book=entry; }
static bool load_cover(const char *path,uint16_t *pixels) {
    if (book_thumbnail(path,pixels)) return true;
    const char *error=NULL;
    Book *book=book_open(path,&error);
    bool ok=book && book_cover(book,pixels,F_COVER_W,F_COVER_H);
    book_close(book); return ok;
}
const uint16_t *appearance_preview(const Entry *entry) {
    if (!entry || entry->directory) return NULL;
    unsigned oldest=0;
    for (unsigned i=0;i<COVER_SLOTS;i++) {
        if (covers[i].book==entry) {
            covers[i].used=++uses;
            return covers[i].valid?covers[i].pixels:NULL;
        }
        if (covers[i].used<covers[oldest].used) oldest=i;
    }
    Cover *c=&covers[oldest]; c->book=entry; c->used=++uses;
    c->valid=load_cover(entry->path,c->pixels);
    return c->valid?c->pixels:NULL;
}
static void expand_cover(const uint16_t *cover) {
    const unsigned crop_h=F_COVER_W*F_HEIGHT/F_WIDTH;
    for (unsigned y=0;y<F_HEIGHT;y++) {
        unsigned sy=(F_COVER_H-crop_h)/2+y*crop_h/F_HEIGHT;
        const uint16_t *row=cover+sy*F_COVER_W;
        for (unsigned x=0;x<F_WIDTH;x++) wallpaper[y*F_WIDTH+x]=row[x*F_COVER_W/F_WIDTH];
    }
}
static bool load_wallpaper(const char *path) {
    uint8_t h[8];
    FILE *f=fopen(path,"rb");
    if (!f) return false;
    bool header=fread(h,1,sizeof h,f)==sizeof h;
    bool raw=header && !memcmp(h,"FWP1",4);
    bool ok=raw && (h[4]|(h[5]<<8))==F_WIDTH && (h[6]|(h[7]<<8))==F_HEIGHT &&
            fread(wallpaper,1,sizeof wallpaper,f)==sizeof wallpaper;
    fclose(f);
    if (raw || !header || memcmp(h,"FOLIO",5)) return ok;
    static uint16_t cover[COVER_PIXELS];
    if (!load_cover(path,cover)) return false;
    expand_cover(cover); return true;
}
static bool load_custom(void) {
    char path[F_PATH];
    return (join_path(path,shelf_root,"background.tns") && load_wallpaper(path)) ||
           (join_path(path,shelf_root,"background.folio.tns") && load_wallpaper(path));
}
static uint16_t mean565(uint16_t a,uint16_t b) {
    return (uint16_t)((a&b)+(((a^b)&0xf7deu)>>1));
}
const uint16_t *appearance_wall(const Entry *selected) {
    if (prefs.background==BG_PLAIN) return NULL;
    if (selected && selected->directory) selected=NULL;
    const Entry *book=prefs.background==BG_CUSTOM?NULL:
        (prefs.background==BG_SELECTED && selected?selected:last_book);
    if (wall_known && book==wall_book) return wall_ok?wallpaper:NULL;
    wall_known=true; wall_book=book; wall_ok=false; custom_missing=false;
    if (prefs.background==BG_CUSTOM) { wall_ok=load_custom(); custom_missing=!wall_ok; }
    else {
        const uint16_t *cover=appearance_preview(book);
        if (cover) { expand_cover(cover); wall_ok=true; }
    }
    if (!wall_ok) return NULL;
    uint16_t paper=prefs.theme==THEME_LIGHT?0xf7bc:0x10c3;
    for (unsigned i=0;i<F_WIDTH*F_HEIGHT;i++) wallpaper[i]=mean565(paper,mean565(paper,wallpaper[i]));
    return wallpaper;
}
bool appearance_custom_missing(void) { return custom_missing; }
