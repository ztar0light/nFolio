#ifndef FOLIO_H
#define FOLIO_H
#include <stdbool.h>
#include <stdint.h>

#define F_WIDTH 320
#define F_HEIGHT 240
#define F_TRIM_UNIT 20u  /* 20 steps per 1x */
#define F_TRIM_MAX 20u   /* steps above fit width */
#define F_BAND 64
#define F_COVER_W 84
#define F_COVER_H 118
#define F_LABEL_WIDTH 264
#define F_LABEL_HEIGHT 20
#define F_LABEL_BYTES (F_LABEL_WIDTH * F_LABEL_HEIGHT / 8)
#define F_PATH 640

typedef struct Book Book;
/* The single-page anchor remains live during a spread, so a quick look at
   facing pages can return to exactly the previous pixel and zoom. */
enum { SPREAD_ON=1, SPREAD_LTR=2, SPREAD_SHIFT=4, SPREAD_ALIGNED=8, SPREAD_PAIR_WIDE=16, SPREAD_DETAIL=32, SPREAD_MASK=63 };
typedef struct { uint32_t y, x; unsigned level, hud, spread, continuous, trim; int spread_x,spread_y; } View;
static inline bool view_equal(View a,View b) {
    return a.y==b.y && a.x==b.x && a.level==b.level && a.hud==b.hud &&
           a.spread==b.spread && a.continuous==b.continuous && a.trim==b.trim &&
           a.spread_x==b.spread_x && a.spread_y==b.spread_y;
}
typedef struct { uint32_t first, last; } Spread;
typedef struct { uint8_t id[16]; uint8_t label[F_LABEL_BYTES]; uint32_t pages; } BookTitle;
typedef struct { uint32_t y, first_page; } Chapter;

typedef struct { uint32_t y; unsigned x, depth; } Destination;
bool book_saved_page(const char *path, View v, uint32_t *page);
unsigned book_bookmarks(const Book *b);
bool book_mark(Book *b, unsigned i, Destination *dest, uint8_t *label);
bool book_links(const Book *b);
bool book_hit(Book *b, View v, int x, int y, Destination *dest);
void view_destination(const Book *b, View *v, Destination dest);
bool book_peek(const char *path, BookTitle *title);
Book *book_open(const char *path, const char **error);
void book_close(Book *book);
const BookTitle *book_title(const Book *book);
uint32_t book_chapters(const Book *book);
uint32_t book_page_count(const Book *book);
unsigned book_levels(const Book *book);
Chapter book_chapter(const Book *book, uint32_t i);
uint32_t book_page(const Book *book, uint32_t i);
bool book_label(Book *book, uint32_t i, uint8_t *label);
uint32_t chapter_at(const Book *book, View v);
uint32_t page_at(const Book *book, View v);
void view_normalize(const Book *book, View *v);
void view_scroll(const Book *book, View *v, int pixels);
void view_pan(const Book *book, View *v, int pixels);
void view_zoom(const Book *book, View *v);
void view_trim(const Book *book, View *v, int direction);
void view_turn(const Book *book, View *v, int direction);
Spread book_spread(const Book *book, View v);
void view_toggle_spread(const Book *book, View *v);
void view_turn_spread(const Book *book, View *v, int direction);
void view_pan_spread(const Book *book,View *v,int dx,int dy);
void view_chapter(const Book *book, View *v, uint32_t i);
bool book_render(Book *book, View v, uint16_t *frame);
bool book_thumbnail(const char *path,uint16_t *pixels);
bool book_cover(Book *book, uint16_t *pixels, unsigned width, unsigned height);
void view_nudge(const Book *book, View *v, int display_pixels);
uint32_t book_decodes(const Book *book);

typedef struct { uint32_t sequence; uint8_t id[16]; View view; } Saved;
bool save_load(const char *folder, const uint8_t *book_id, Saved *saved);
uint32_t folio_checksum(const uint8_t *bytes,unsigned length);
bool save_commit(const char *folder, const uint8_t *book_id, const uint8_t *id, View view);
bool join_path(char *out, const char *folder, const char *name);
#endif
