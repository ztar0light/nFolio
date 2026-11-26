#include "folio.h"
#include "device.h"
#include "shelf.h"
#include "appearance.h"
#include "ui_font.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>


#define PAPER 0xf7bc
#define INK 0x18e4
#define BLUE 0x1b4d
#define PAPER_WHITE 0xffff
#define MUTED 0x638c
#define RED 0x98e4
#define ITEMS 7
#define TICK 20u
#define IDLE_SAVE 700u
#define ACTIVE_SAVE 5000u
#define DOUBLE_CLICK_MS 400u

static uint16_t frame[F_WIDTH*F_HEIGHT];
typedef struct {
    Key held; uint32_t now,last_repeat,release_at;
    bool repeated,releasing;
    Pad pad; int x,y,dx,dy; bool moved,visible,modifier_changed,pointer_click;
} Keyboard;
typedef enum { DONE, BOOKS, FAULT } Result;
static void rect(int x,int y,int w,int h,uint16_t color) {
    for (int j=0;j<h;j++) for(int i=0;i<w;i++) frame[(y+j)*F_WIDTH+x+i]=color;
}
static void small(int x,int y,const char *s,uint16_t color) {
    for (;*s && x+8<=F_WIDTH; s++, x+=8) {
        unsigned c=(unsigned char)*s;
        if (c<32 || c>126) c='?';
        for (int r=0;r<14;r++) for (int k=0;k<8;k++)
            if (ui_font[c-32][r] & (128u>>k)) frame[(y+r)*F_WIDTH+x+k]=color;
    }
}
static void bitmap(int x,int y,int width,const uint8_t *data,uint16_t color) {
    for(int r=0;r<F_LABEL_HEIGHT;r++) for(int k=0;k<width;k++)
        if (data[r*(F_LABEL_WIDTH/8)+k/8] & (128u>>(k%8))) frame[(y+r)*F_WIDTH+x+k]=color;
}
static int clamp(int v,int lo,int hi) { return v<lo?lo:v>hi?hi:v; }
static bool direction(Key key) { return key>=K_UP && key<=K_RIGHT; }
static Key next_key(Keyboard *k,bool repeat_vertical,bool repeat_pan) {
    device_sleep(TICK);
    Pad pad={0}; Key raw=device_poll(&pad);
    k->now=device_millis();
    k->modifier_changed=pad.ctrl!=k->pad.ctrl;
    k->visible=pad.ctrl; k->moved=false;
    /* A quick click may arrive on release, after Ctrl has also lifted. */
    bool click_modifier=pad.ctrl || (k->pad.ctrl && k->pad.pressed && !pad.pressed);
    k->pointer_click=click_modifier && (raw==K_CENTER || (pad.contact && direction(raw)));
    if (!pad.ctrl || !pad.contact || !k->pad.contact || k->modifier_changed || pad.pressed || k->pad.pressed) k->dx=k->dy=0;
    else if (raw==K_NONE) {
        k->dx+=pad.x-k->pad.x; k->dy+=pad.y-k->pad.y;
        int dx=k->dx/2, dy=k->dy/2;
        k->dx-=2*dx; k->dy-=2*dy;
        int x=clamp(k->x+dx,0,F_WIDTH-1), y=clamp(k->y+dy,0,F_HEIGHT-1);
        k->moved=x!=k->x || y!=k->y; k->x=x; k->y=y;
    }
    k->pad=pad;
    if (pad.scan_failed) { k->releasing=false; return K_NONE; }
    if (raw==K_NONE && (direction(k->held) || k->held==K_CENTER || k->held==K_MENU ||
                        k->held==K_TRIM_IN || k->held==K_TRIM_OUT)) {
        if (!k->releasing) { k->release_at=k->now; k->releasing=true; return K_NONE; }
        if (k->now-k->release_at<15) return K_NONE;
    }
    k->releasing=false;
    if (!repeat_vertical && !repeat_pan && direction(raw) && direction(k->held)) return K_NONE;
    if (raw!=k->held) {
        k->held=raw; k->last_repeat=k->now; k->repeated=false; return raw;
    }
    bool repeating=(repeat_vertical && (raw==K_UP || raw==K_DOWN)) ||
                   (repeat_pan && (raw==K_LEFT || raw==K_RIGHT));
    if (!repeating || pad.ctrl) return K_NONE;
    if (k->now-k->last_repeat<(k->repeated?120u:460u)) return K_NONE;
    k->last_repeat=k->now; k->repeated=true;
    return raw;
}
static void cursor(Keyboard *k) {
    if (!k->visible) return;
    for (int j=0;j<13 && k->y+j<F_HEIGHT;j++) {
        int width=j<9?j/2+1:2;
        for (int i=0;i<width && k->x+i<F_WIDTH;i++)
            frame[(k->y+j)*F_WIDTH+k->x+i]=!i || i==width-1 || !j ? 0 : PAPER_WHITE;
    }
}
typedef enum { LIBRARY, CONTENTS, COMMANDS, STYLE } MenuKind;
typedef struct {
    MenuKind kind; const char *title; int count; Entry **items; Book *book;
    bool can_up; const Entry *reference;
} Menu;
enum { BACK=-1, POWER=-2, TO_COMMANDS=-3 };
static const char *commands[]={"Bookmarks / chapters","Bookshelf","Appearance","Continue reading","Exit Folio"};
static const char *background_names[]={"Plain","Last book","Selected book","Custom file"};
static const char *theme_names[]={"Light","Dark menus","Dark menus and pages"};
static uint8_t row_labels[ITEMS][F_LABEL_BYTES], row_indent[ITEMS];
static bool row_valid[ITEMS], style_write_failed;
typedef struct {
    const uint16_t *wall;
    bool sidebar,cursor_drawn;
    int cx,cy,cw,ch;
    uint16_t underneath[13][5];
} MenuScene;

static const Entry *menu_focus(const Menu *m,int selected) {
    if (m->kind==LIBRARY && m->count) return m->items[selected];
    return m->reference;
}
static uint16_t menu_paper(void) { return appearance_get().theme==THEME_LIGHT?PAPER:0x10c3; }
static uint16_t menu_ink(void) { return appearance_get().theme==THEME_LIGHT?INK:0xef7d; }
static uint16_t menu_bar(void) { return appearance_get().theme==THEME_LIGHT?INK:0x0842; }
static uint16_t menu_muted(void) { return appearance_get().theme==THEME_LIGHT?MUTED:0xb617; }
static void menu_region(const MenuScene *s,int x,int y,int w,int h) {
    if (!s->wall) { rect(x,y,w,h,menu_paper()); return; }
    for (int row=0;row<h;row++) {
        int at=(y+row)*F_WIDTH+x;
        memcpy(frame+at,s->wall+at,(size_t)w*sizeof(*frame));
    }
}
static void remove_menu_cursor(MenuScene *s) {
    if (!s->cursor_drawn) return;
    for (int row=0;row<s->ch;row++)
        memcpy(frame+(s->cy+row)*F_WIDTH+s->cx,s->underneath[row],(size_t)s->cw*sizeof(*frame));
    s->cursor_drawn=false;
}
static void present_menu(MenuScene *s,Keyboard *k) {
    if (k->visible) {
        s->cx=k->x; s->cy=k->y;
        s->cw=clamp(F_WIDTH-k->x,0,5); s->ch=clamp(F_HEIGHT-k->y,0,13);
        for (int row=0;row<s->ch;row++)
            memcpy(s->underneath[row],frame+(s->cy+row)*F_WIDTH+s->cx,(size_t)s->cw*sizeof(*frame));
        s->cursor_drawn=true; cursor(k);
    }
    device_blit(frame);
}
static void cache_contents(const Menu *m,int first) {
    if (m->kind!=CONTENTS) return;
    bool marks=book_bookmarks(m->book)>0;
    for (int row=0;row<ITEMS && first+row<m->count;row++) {
        Destination d={0}; unsigned item=(unsigned)(first+row);
        row_valid[row]=marks?book_mark(m->book,item,&d,row_labels[row]):
                             book_label(m->book,item,row_labels[row]);
        row_indent[row]=(uint8_t)clamp((int)d.depth,0,6);
    }
}
static void menu_row(const Menu *m,int i,int selected,const MenuScene *s) {
    int row=i%ITEMS, top=37+row*25, edge=s->sidebar?219:316;
    bool active=i==selected;
    uint16_t color=active?PAPER_WHITE:menu_ink();
    if (active) rect(4,top,edge-4,24,BLUE);
    else menu_region(s,4,top,edge-4,24);
    if (m->kind==COMMANDS) { small(12,top+5,commands[i],color); return; }
    if (m->kind==STYLE) {
        Appearance p=appearance_get(); char label[39];
        if (!i) snprintf(label,sizeof label,"Background  %s",background_names[p.background]);
        else if (i==1) snprintf(label,sizeof label,"Theme  %s",theme_names[p.theme]);
        else strcpy(label,"Back");
        small(12,top+5,label,color); return;
    }
    if (m->kind==CONTENTS) {
        int x=12+row_indent[row]*12;
        if (row_valid[row]) bitmap(x,top+2,clamp(314-x,0,F_LABEL_WIDTH),row_labels[row],color);
        return;
    }
    const Entry *e=m->items[i];
    if (e->directory) {
        rect(11,top+6,8,3,color); rect(11,top+9,16,10,color);
        char name[35]; snprintf(name,sizeof name,"%.*s",s->sidebar?22:34,entry_name(e));
        small(36,top+5,name,color);
    } else if (s->sidebar) bitmap(10,top+2,edge-17,e->title->label,color);
    else {
        char number[32];
        if (e->saved_page) snprintf(number,sizeof number,"p%lu/%lu",(unsigned long)e->saved_page,(unsigned long)e->title->pages);
        else strcpy(number,"New");
        int right=edge-7-(int)strlen(number)*8;
        bitmap(10,top+2,clamp(right-15,0,F_LABEL_WIDTH),e->title->label,color);
        small(right,top+5,number,active?PAPER_WHITE:menu_muted());
    }
}
static int count_left(const Menu *m) {
    int digits=1;
    for (int n=m->count;n>=10;n/=10) digits++;
    return 313-8*(2*digits+1);
}
static void menu_count(const Menu *m,int selected) {
    char number[32];
    snprintf(number,sizeof number,"%d/%d",m->count?selected+1:0,m->count);
    int start=count_left(m);
    rect(start,0,F_WIDTH-start,34,menu_bar());
    small(313-8*(int)strlen(number),10,number,PAPER_WHITE);
}
static void menu_sidebar(const Entry *e,const MenuScene *s,bool load) {
    menu_region(s,223,37,93,171);
    if (!e || e->directory) return;
    if (load) {
        const uint16_t *pic=appearance_preview(e);
        if (pic) for (unsigned y=0;y<F_COVER_H;y++)
            memcpy(frame+(48+y)*F_WIDTH+229,pic+y*F_COVER_W,F_COVER_W*sizeof(*frame));
    }
    char line[24]="New";
    if (e->saved_page) {
        snprintf(line,sizeof line,"of %lu",(unsigned long)e->title->pages);
        small(271-(int)strlen(line)*4,188,line,menu_muted());
        snprintf(line,sizeof line,"Page %lu",(unsigned long)e->saved_page);
    }
    small(271-(int)strlen(line)*4,172,line,menu_muted());
}
static void paint_menu(const Menu *m,int selected,MenuScene *s,bool load,bool labels) {
    const Entry *focus=menu_focus(m,selected);
    if (load) s->wall=appearance_wall(focus);
    s->sidebar=m->kind==LIBRARY && focus && !focus->directory;
    menu_region(s,0,0,F_WIDTH,F_HEIGHT);
    rect(0,0,F_WIDTH,34,menu_bar());
    char title[35]; snprintf(title,sizeof title,"%.*s",clamp((count_left(m)-12)/8,0,34),m->title);
    small(9,10,title,PAPER_WHITE); menu_count(m,selected);
    int first=selected/ITEMS*ITEMS;
    if (labels) cache_contents(m,first);
    for (int i=first;i<m->count && i<first+ITEMS;i++) menu_row(m,i,selected,s);
    if (s->sidebar) menu_sidebar(focus,s,load);
    if (!m->count) small(12,55,"This section is empty.",menu_muted());
    if (m->kind==STYLE) {
        if (style_write_failed) small(10,177,"Could not save appearance.",menu_ink());
        else if (appearance_custom_missing()) small(10,177,"Add background.tns to folio.",menu_ink());
    }
    rect(0,214,F_WIDTH,26,menu_bar());
    small(10,220,m->kind==LIBRARY&&!m->can_up?"Exit":"Back",PAPER_WHITE);
    small(115,220,"< Prev",PAPER_WHITE); small(224,220,"Next >",PAPER_WHITE);
}
static int appearance_menu(Keyboard *k,const Entry *focus);
static int choose(const Menu *m,int selected,Keyboard *k) {
    selected=clamp(selected,0,m->count?m->count-1:0);
    MenuScene scene={0};
    k->visible=k->pad.ctrl; paint_menu(m,selected,&scene,true,true); present_menu(&scene,k);
    bool deferred=false; uint32_t last_move=0;
    for (;;) {
        Key key=next_key(k,false,false); int old=selected;
        if (k->pointer_click && direction(key)) key=K_CENTER;
        if (key==K_ESC) return BACK;
        if (key==K_EXIT) return POWER;
        if (key==K_MENU) {
            if (m->kind==CONTENTS) return TO_COMMANDS;
            if (m->kind!=LIBRARY) return BACK;
            if (appearance_menu(k,menu_focus(m,selected))==POWER) return POWER;
            scene.cursor_drawn=false; deferred=false;
            paint_menu(m,selected,&scene,true,true); present_menu(&scene,k); continue;
        }
        if (key==K_ENTER && m->count) return selected;
        if (k->moved || (key==K_CENTER && k->pointer_click)) {
            if (k->y>=37 && k->y<37+25*ITEMS && (!scene.sidebar || k->x<220)) {
                int row=selected/ITEMS*ITEMS+(k->y-37)/25;
                if (row<m->count) {
                    selected=row;
                    if (key==K_CENTER) return row;
                }
            } else if (key==K_CENTER && k->y>=214) {
                if (k->x<100) return BACK;
                key=k->x<210?K_PREV:K_NEXT;
            }
        } else if (key==K_CENTER && m->count) return selected;
        if (key==K_UP || key==K_DOWN || key==K_RIGHT || key==K_LEFT || key==K_NEXT || key==K_PREV) {
            if (key==K_NEXT || key==K_PREV) selected+=key==K_NEXT?ITEMS:-ITEMS;
            else selected+=(key==K_UP || key==K_LEFT)?-1:1;
            selected=clamp(selected,0,m->count?m->count-1:0);
        }
        if (k->moved) last_move=k->now;
        bool changed=selected!=old;
        bool idle_preview=deferred && !k->moved && k->now-last_move>=160u;
        if (!changed && !idle_preview && !k->moved && !k->modifier_changed) continue;
        remove_menu_cursor(&scene);
        if (changed) {
            const Entry *focus=menu_focus(m,selected);
            bool library=m->kind==LIBRARY;
            deferred=library && k->moved;
            bool sidebar=library && focus && !focus->directory;
            if (old/ITEMS!=selected/ITEMS || scene.sidebar!=sidebar || (library && !deferred)) {
                paint_menu(m,selected,&scene,!deferred,old/ITEMS!=selected/ITEMS);
            } else {
                menu_row(m,old,selected,&scene); menu_row(m,selected,selected,&scene);
                menu_count(m,selected);
                if (sidebar) menu_sidebar(focus,&scene,false);
            }
        } else if (idle_preview) {
            const Entry *focus=menu_focus(m,selected);
            if (appearance_get().background==BG_SELECTED) paint_menu(m,selected,&scene,true,false);
            else if (scene.sidebar) menu_sidebar(focus,&scene,true);
            deferred=false;
        }
        present_menu(&scene,k);
    }
}
static int appearance_menu(Keyboard *k,const Entry *focus) {
    Menu m={STYLE,"Appearance",3,NULL,NULL,false,focus};
    int selected=0; style_write_failed=false;
    for (;;) {
        selected=choose(&m,selected,k);
        if (selected<0 || selected==2) return selected==POWER?POWER:BACK;
        Appearance p=appearance_get();
        if (!selected) p.background=(Background)((p.background+1)%BG_COUNT);
        else p.theme=(Theme)((p.theme+1)%THEME_COUNT);
        style_write_failed=!appearance_save(p);
    }
}
static int bookshelf(Shelf *s,int current,Keyboard *k) {
    int folder=current>=0?s->entries[current].parent:-1, focus=current;
    for (;;) {
        Entry **items; int count=shelf_children(s,folder,&items);
        if (count<0) return POWER;
        int selected=0;
        for (int i=0;i<count;i++) if (items[i]-s->entries==focus) selected=i;
        Menu m={LIBRARY,folder<0?"folio":entry_name(&s->entries[folder]),count,items,NULL,folder>=0,NULL};
        int choice=choose(&m,selected,k);
        int target=choice>=0?(int)(items[choice]-s->entries):choice;
        free(items);
        if (target==POWER) return POWER;
        if (target==BACK) {
            if (folder<0) return POWER;
            focus=folder; folder=s->entries[folder].parent; continue;
        }
        if (!s->entries[target].directory) return target;
        folder=target; focus=-1;
    }
}
static void format_trim(char *text,size_t capacity,unsigned steps) {
    unsigned percent=100+steps*100/F_TRIM_UNIT;
    snprintf(text,capacity,"%u.%02ux",percent/100,percent%100);
}
static void hud(Book *book, View v) {
    uint32_t c=chapter_at(book,v), p=page_at(book,v);
    Chapter ch=book_chapter(book,c);
    uint32_t end=c+1<book_chapters(book)?book_chapter(book,c+1).first_page:book_page_count(book);
    char info[40]; uint8_t label[F_LABEL_BYTES];
    if (v.spread&SPREAD_ON) {
        Spread pair=book_spread(book,v);
        const char *order=(v.spread&SPREAD_LTR)?"LTR":"RTL";
        if (pair.first==pair.last) snprintf(info,sizeof(info),"p%lu %s",(unsigned long)pair.first+1,order);
        else snprintf(info,sizeof(info),"p%lu-%lu %s",(unsigned long)pair.first+1,(unsigned long)pair.last+1,order);
        if (v.spread&SPREAD_DETAIL) strcat(info," 1.5x");
    } else {
        snprintf(info,sizeof(info),"%lu/%lu%s",(unsigned long)(p-ch.first_page+1),
            (unsigned long)(end-ch.first_page),v.level?" 1.5x":"");
    }
    if (v.trim && !v.level && !(v.spread&SPREAD_ON)) {
        size_t used=strlen(info);
        info[used++]=' '; format_trim(info+used,sizeof(info)-used,v.trim);
    }
    int right=314-8*(int)strlen(info);
    if (v.spread&SPREAD_ON) {
        rect(0,196,320,20,INK);
        small(5,199,v.spread&SPREAD_DETAIL?"Arrows pan   +/- pair   Z fit":"D close   R order   P pair   W wide",PAPER_WHITE);
    }
    rect(0,216,320,24,INK);
    if (book_label(book,c,label)) bitmap(3,218,right-8<F_LABEL_WIDTH?right-8:F_LABEL_WIDTH,label,PAPER_WHITE);
    small(right,221,info,PAPER_WHITE);
}
static bool paint(Book *b,View v,const char *notice,bool failed,Keyboard *k) {
    if (!book_render(b,v,frame)) return false;
    if (appearance_get().theme==THEME_DARK_ALL)
        for (unsigned i=0;i<F_WIDTH*F_HEIGHT;i++) frame[i]=(uint16_t)~frame[i];
    if (v.hud) hud(b,v);
    if (notice || failed) {
        rect(0,0,320,20,failed?RED:BLUE); small(7,3,failed?"Bookmark write failed. S retries.":notice,PAPER_WHITE);
    }
    cursor(k);
    device_blit(frame);
    return true;
}
static void navigate(const Book *b,View *v,Key key) {
    if (key==K_SPREAD) { view_toggle_spread(b,v); return; }
    if (key==K_TRIM_IN || key==K_TRIM_OUT) { view_trim(b,v,key==K_TRIM_IN?1:-1); return; }
    if (v->spread&SPREAD_ON) {
        if ((v->spread&SPREAD_DETAIL) && direction(key)) {
            int dx=key==K_LEFT?-12:key==K_RIGHT?12:0;
            int dy=key==K_UP?-12:key==K_DOWN?12:0;
            view_pan_spread(b,v,dx,dy); return;
        }
        bool ltr=!!(v->spread&SPREAD_LTR);
        switch (key) {
            case K_WIDE: v->spread^=SPREAD_PAIR_WIDE; v->spread_x=v->spread_y=0; return;
            case K_ORDER: v->spread^=SPREAD_LTR; v->spread_x=v->spread_y=0; return;
            case K_PAIR: v->spread^=SPREAD_SHIFT; v->spread_x=v->spread_y=0; return;
            case K_LEFT: view_turn_spread(b,v,ltr?-1:1); return;
            case K_RIGHT: view_turn_spread(b,v,ltr?1:-1); return;
            case K_DOWN: case K_NEXT: case K_PLUS: view_turn_spread(b,v,1); return;
            case K_UP: case K_PREV: case K_MINUS: view_turn_spread(b,v,-1); return;
            case K_ZOOM: v->spread^=SPREAD_DETAIL; v->spread_x=v->spread_y=0; return;
            default: break;
        }
    }
    switch(key) {
        case K_UP: view_nudge(b,v,-6); break;
        case K_DOWN: view_nudge(b,v,6); break;
        case K_PREV: view_scroll(b,v,-216); break;
        case K_NEXT: view_scroll(b,v,216); break;
        case K_MINUS: view_turn(b,v,-1); break;
        case K_PLUS: view_turn(b,v,1); break;
        case K_LEFT: if (v->level) view_pan(b,v,-16); else view_turn(b,v,-1); break;
        case K_RIGHT: if (v->level) view_pan(b,v,16); else view_turn(b,v,1); break;
        case K_ZOOM: view_zoom(b,v); break;
        case K_TAB: v->hud=!v->hud; break;
        default: break;
    }
}
typedef struct { bool down,pending; uint32_t released_at; } Clicks;
static Key gesture(Clicks *c,const Keyboard *keys,Key key,bool *single) {
    *single=false;
    bool other=key!=K_NONE && key!=K_OTHER && key!=K_CENTER;
    if (c->pending && (other || keys->now-c->released_at>=DOUBLE_CLICK_MS)) {
        c->pending=false; *single=true;
    }
    if (key==K_CENTER) {
        if (c->pending) { *c=(Clicks){0}; return K_SPREAD; }
        c->down=true;
        return K_NONE;
    }
    if (c->down && keys->held!=K_CENTER) {
        c->down=false;
        if (other) *single=true;
        else { c->pending=true; c->released_at=keys->now; }
    }
    return key;
}
static bool store_view(const char *root,Entry *entry,Book *b,View v) {
    if (!save_commit(root,entry->title->id,entry->title->id,v)) return false;
    entry->saved_page=page_at(b,v)+1; return true;
}
static Key reader_menu(Book *book,View *view,Keyboard *keys) {
    Menu menu={COMMANDS,"Reader menu",5,NULL,book,false,NULL};
    int selected=0;
    for (;;) {
        int action=choose(&menu,selected,keys);
        if (action==1) return K_BOOKS;
        if (action==4 || action==POWER) return K_EXIT;
        if (action==2) {
            if (appearance_menu(keys,NULL)==POWER) return K_EXIT;
            selected=2; continue;
        }
        if (action!=0) return K_NONE;

        bool marks=book_bookmarks(book)>0;
        Menu contents={CONTENTS,marks?"PDF bookmarks":"Chapters",(int)(marks?book_bookmarks(book):book_chapters(book)),NULL,book,false,NULL};
        int item=choose(&contents,marks?0:(int)chapter_at(book,*view),keys);
        if (item==TO_COMMANDS) continue;
        if (item==POWER) return K_EXIT;
        if (item>=0) {
            Destination d;
            if (!marks) view_chapter(book,view,(uint32_t)item);
            else if (book_mark(book,(uint32_t)item,&d,NULL)) view_destination(book,view,d);
        }
        return K_NONE;
    }
}
static Result reading(const char *root,Entry *entry,Book *b,Keyboard *keys,const char **err) {
    appearance_reading(entry);
    const uint8_t *id=entry->title->id;
    Saved old_save; bool known=save_load(root,id,&old_save);
    View v=known?old_save.view:(View){0};
    view_normalize(b,&v);
    bool dirty=!known,failed=false;
    unsigned quiet=0,age=0,notice_time=0; const char *notice=NULL;
    char trim_notice[20]="Trim ";
    bool recent_failed=!save_commit(root,NULL,id,(View){0});
    if (recent_failed) { notice="Last book write failed."; notice_time=2000; }
    Clicks clicks={0}; keys->visible=keys->pad.ctrl;
    *err="Could not read this book strip. Transfer the pack again.";
    if (!paint(b,v,notice,false,keys)) return FAULT;
    for (;;) {
        bool single_page=!(v.spread&SPREAD_ON);
        bool pair_detail=(v.spread&(SPREAD_ON|SPREAD_DETAIL))==(SPREAD_ON|SPREAD_DETAIL);
        Key key=next_key(keys,single_page || pair_detail,(single_page && v.level) || pair_detail);
        bool redraw=keys->moved || keys->modifier_changed, single=false;
        View old=v;
        if (keys->modifier_changed) clicks=(Clicks){0};
        if (keys->pad.ctrl || keys->pointer_click) {
            clicks=(Clicks){0};
            if (keys->pointer_click && (key==K_CENTER || direction(key))) {
                Destination d;
                if (book_hit(b,v,keys->x,keys->y,&d)) view_destination(b,&v,d);
                else if (key==K_CENTER) {
                    v.continuous=(v.spread&SPREAD_ON) || !v.continuous;
                    v.spread&=~(SPREAD_ON|SPREAD_DETAIL); v.spread_x=v.spread_y=0;
                    if (!v.continuous) view_nudge(b,&v,0);
                    notice=v.continuous?"Continuous scroll":"Paged reading"; notice_time=1000;
                }
                key=K_NONE; redraw=true;
            }
        } else {
            key=gesture(&clicks,keys,key,&single);
            if (single) {
                navigate(b,&v,K_ZOOM);
                dirty=true; quiet=0; redraw=true;
            }
        }
        if (notice_time) { notice_time-=TICK; if (!notice_time) { notice=NULL; redraw=true; } }
        if (key==K_ESC || key==K_BOOKS || key==K_EXIT || key==K_MENU) {
            if (dirty || failed) {
                failed=!store_view(root,entry,b,v);
                if (!failed) { dirty=false; quiet=age=0; }
            }
            if (key==K_MENU) {
                key=reader_menu(b,&v,keys);
                keys->visible=keys->pad.ctrl; redraw=true;
            }
            if (key==K_ESC || key==K_BOOKS || key==K_EXIT) {
                if (failed && key!=K_EXIT) {
                    if (!paint(b,v,NULL,true,keys)) return FAULT;
                    continue;
                }
                *err=failed?"Could not save this position. Check free storage.":(recent_failed?"Could not save the last-opened book.":NULL);
                return key==K_EXIT?DONE:BOOKS;
            }
        }
        if (key!=K_NONE && keys->visible) redraw=true;
        navigate(b,&v,key);
        if ((v.spread&SPREAD_ON) && notice==trim_notice) {
            notice=NULL; notice_time=0; redraw=true;
        }
        if (!(v.spread&SPREAD_ON) && (key==K_TRIM_IN || key==K_TRIM_OUT)) {
            format_trim(trim_notice+5,sizeof(trim_notice)-5,v.trim);
            notice=trim_notice; notice_time=1000; redraw=true;
        }
        if (!view_equal(old,v)) { dirty=true; quiet=0; redraw=true; }
        if (dirty) { quiet+=TICK; age+=TICK; }
        if (redraw && !paint(b,v,notice,failed,keys)) return FAULT;
        if (key==K_SAVE || (dirty && (quiet>=IDLE_SAVE || age>=ACTIVE_SAVE))) {
            bool last_failed=failed;
            failed=!store_view(root,entry,b,v);
            if (!failed && (recent_failed || key==K_SAVE)) recent_failed=!save_commit(root,NULL,id,(View){0});
            dirty=failed; quiet=age=0;
            if (key==K_SAVE && !failed) { notice=recent_failed?"Saved. Last book write failed.":"Position saved"; notice_time=1100; }
            if ((key==K_SAVE || failed!=last_failed) && !paint(b,v,notice,failed,keys)) return FAULT;
        }
    }
}
int folio_run(const char *executable) {
    char root[F_PATH]; const char *slash=strrchr(executable,'/');
    size_t n=slash?(size_t)(slash-executable):0;
    if (n>=sizeof(root)) { device_message("Folder path is too long."); return 1; }
    if (n) { memcpy(root,executable,n); root[n]=0; } else strcpy(root,slash?"/":".");
    Shelf s={0}; const char *err=NULL;
    if (!shelf_scan(root,&s)) err="Could not read the folio folder.";
    else if (!s.count) err="Put a .folio.tns book beside Folio.tns or inside a section.";
    if (err) { shelf_free(&s); device_message(err); return 1; }
    if (!device_display_on()) { shelf_free(&s); device_message("Folio needs a colour Nspire display."); return 1; }
    appearance_load(root);
    Keyboard keys={.x=160,.y=120}; keys.held=device_poll(&keys.pad);
    int which=-1; Saved recent;
    bool resume=save_load(root,NULL,&recent);
    for (int i=0;i<s.count;i++) {
        Entry *e=&s.entries[i];
        if (e->directory) continue;
        if (resume && !memcmp(recent.id,e->title->id,16)) { which=i; break; }
        if (s.count==1) which=i;
    }
    bool select=which<0;
    for (;;) {
        if (select) { which=bookshelf(&s,which,&keys); if (which<0) break; }
        Entry *entry=&s.entries[which]; Book *b=book_open(entry->path,&err);
        Result result=b?reading(root,entry,b,&keys,&err):FAULT;
        book_close(b);
        if (result==DONE) break;
        if (result==FAULT) {
            if (s.books<=1) break;
            device_display_off(); device_message(err); err=NULL;
            if (!device_display_on()) { err="Could not reopen the display."; break; }
            keys.held=device_poll(&keys.pad);
        }
        select=true;
    }
    device_display_off(); shelf_free(&s);
    if (err) { device_message(err); return 1; }
    return 0;
}
