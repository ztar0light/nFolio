#include "device.h"
#include "folio.h"
#include <libndls.h>
#include <string.h>
#include <strings.h>

void _init(void) {}
void _fini(void) {}

typedef struct { const t_key *key; Key result; } Binding;
static const Binding keypad[]={
    {&KEY_NSPIRE_ESC,K_ESC},{&KEY_NSPIRE_MENU,K_MENU},{&KEY_NSPIRE_HOME,K_BOOKS},
    {&KEY_NSPIRE_ENTER,K_ENTER},{&KEY_NSPIRE_RET,K_ENTER},{&KEY_NSPIRE_B,K_BOOKS},
    {&KEY_NSPIRE_D,K_SPREAD},{&KEY_NSPIRE_R,K_ORDER},{&KEY_NSPIRE_W,K_WIDE},
    {&KEY_NSPIRE_P,K_PAIR},{&KEY_NSPIRE_Z,K_ZOOM},{&KEY_NSPIRE_S,K_SAVE},
    {&KEY_NSPIRE_TAB,K_TAB},{&KEY_NSPIRE_SPACE,K_NEXT},{&KEY_NSPIRE_DEL,K_PREV},
    {&KEY_NSPIRE_PLUS,K_PLUS},{&KEY_NSPIRE_MINUS,K_MINUS},{&KEY_NSPIRE_8,K_UP},
    {&KEY_NSPIRE_2,K_DOWN},{&KEY_NSPIRE_4,K_LEFT},{&KEY_NSPIRE_6,K_RIGHT},
    {&KEY_NSPIRE_MULTIPLY,K_TRIM_IN},{&KEY_NSPIRE_DIVIDE,K_TRIM_OUT}
};
static Key pressed_zone;
static bool centre_pending,release_seen;
static Key pad_zone(const Pad *p) {
    int x=p->x-F_WIDTH/2, y=p->y-F_HEIGHT/2;
    /* The centre ellipse spans one fifth of each pad dimension. */
    const int rx=F_WIDTH/10, ry=F_HEIGHT/10;
    int nx=x*ry, ny=y*rx, radius=rx*ry;
    if (nx*nx+ny*ny<=radius*radius) return K_CENTER;
    int ax=x<0?-x:x, ay=y<0?-y:y;
    if (ax*F_HEIGHT>ay*F_WIDTH) return x<0?K_LEFT:K_RIGHT;
    return y<0?K_UP:K_DOWN;
}

static Key touch_key(const Pad *p) {
    Key zone=p->contact?pad_zone(p):K_NONE;
    if (!p->pressed) {
        Key tap=K_NONE;
        if (centre_pending) {
            /* One located sample is enough for a quick click. A finger
               already outside the centre on release resolves the direction. */
            tap=p->contact?zone:K_CENTER;
            pressed_zone=tap;
        }
        centre_pending=false;
        if (release_seen) pressed_zone=K_NONE;
        release_seen=true;
        return tap;
    }
    release_seen=false;
    if (pressed_zone==K_NONE) {
        /* Give the centre a second sample to settle. A located arrow wins
           immediately. A switch report alone leaves the press unassigned. */
        if (zone==K_CENTER && !centre_pending) centre_pending=true;
        else {
            pressed_zone=zone;
            centre_pending=false;
        }
    }
    return pressed_zone;
}

Key device_poll(Pad *p) {
    *p=(Pad){0};
    if (on_key_pressed()) return K_EXIT;
    /* The colour models use active-high bits. Read each matrix row once.
       Do not wait for key-up, including for keys without a reader binding. */
    volatile uint16_t *matrix=(volatile uint16_t *)0x900e0010;
    uint16_t rows[8];
    for (unsigned i=0;i<8;i++) rows[i]=matrix[i];
    p->ctrl=!!(rows[(KEY_NSPIRE_CTRL.tpad_row-0x10)/2] & KEY_NSPIRE_CTRL.tpad_col);
    for (unsigned i=0;i<sizeof(keypad)/sizeof(keypad[0]);i++) {
        const t_key *k=keypad[i].key;
        if (rows[(k->tpad_row-0x10)/2] & k->tpad_col) {
            /* A keypad action consumes an overlapping pad press. */
            if (release_seen) pressed_zone=K_NONE;
            else if (centre_pending || pressed_zone!=K_NONE) pressed_zone=K_OTHER;
            centre_pending=false;
            return keypad[i].result;
        }
    }
    touchpad_report_t report={0};
    const touchpad_info_t *info=touchpad_getinfo();
    if (!info || !info->width || !info->height || touchpad_scan(&report)!=0) {
        centre_pending=false; p->scan_failed=true; return K_NONE;
    }
    p->pressed=report.pressed!=0;
    if (report.contact && report.x<=info->width && report.y<=info->height && (report.x || report.y)) {
        p->contact=true;
        p->x=(int)((unsigned)report.x*F_WIDTH/info->width);
        p->y=F_HEIGHT-(int)((unsigned)report.y*F_HEIGHT/info->height);
    }
    return touch_key(p);
}

/* Use an idle SP804 channel as a polled timebase. A stalled clock must
   never strand the input loop. Neither path changes CPU interrupt masks. */
static volatile uint32_t *timer,*clock_select;
static unsigned old_clock,old_control,old_load,loops_per_ms;
static uint32_t last_count,milliseconds,fraction;
static bool ticking;
static void clock_start(void) {
    static const uintptr_t blocks[]={0x900d0000u,0x90010000u,0x900c0000u};
    loops_per_ms=is_cx2?1600:600;
    for (unsigned i=0;i<sizeof(blocks)/sizeof(blocks[0]);i++) {
        volatile uint32_t *base=(volatile uint32_t *)blocks[i];
        bool first_busy=(base[2]&0x80u)!=0, second_busy=(base[10]&0x80u)!=0;
        if (first_busy && second_busy) continue;
        uint32_t speed=base[32];
        /* Bits 0-1 select the input clock for the block. Never retime a
           running sibling. The normal 0x900d block already uses 32 kHz. */
        if (!(speed&2u) && (first_busy || second_busy)) continue;
        timer=base+(first_busy?8:0);
        if (!(speed&2u)) {
            clock_select=base+32; old_clock=speed&3u;
            *clock_select=(speed&~3u)|2u;
        }
        old_control=timer[2]; old_load=timer[0];
        timer[0]=0xffffffffu; timer[2]=0x82u;
        last_count=timer[1]; ticking=true;
        return;
    }
}
static void clock_stop(void) {
    if (timer) {
        timer[2]=0;
        timer[0]=old_load;
        if (clock_select) *clock_select=(*clock_select&~3u)|old_clock;
        timer[2]=old_control;
    }
    timer=NULL; clock_select=NULL; ticking=false;
}
uint32_t device_millis(void) {
    if (ticking) {
        uint32_t value=timer[1], ticks=last_count-value;
        uint64_t scaled=(uint64_t)ticks*1000u+fraction;
        milliseconds+=(uint32_t)(scaled>>15); fraction=(uint32_t)scaled&32767u;
        last_count=value;
    }
    return milliseconds;
}
static inline void short_pause(void) {
    unsigned n=64;
    __asm__ volatile("1: subs %0,%0,#1\n bne 1b":"+r"(n)::"cc");
}
void device_sleep(unsigned ms) {
    if (ticking) {
        uint32_t start=timer[1], previous=start, ticks=(ms*32768u+999u)/1000u;
        unsigned stalled=0;
        while (start-previous<ticks) {
            short_pause();
            uint32_t now=timer[1];
            if (now!=previous) stalled=0;
            else if (++stalled>=2048) {
                device_millis(); ticking=false;
                ms-=((start-previous)*1000u)>>15;
                break;
            }
            previous=now;
        }
        if (ticking) return;
    }
    for (unsigned i=0;i<ms*loops_per_ms;i++) short_pause();
    milliseconds+=ms;
}
bool device_display_on(void) {
    if (!has_colors || !lcd_init(SCR_320x240_565)) return false;
    /* The switch may still be down from opening Folio in My Documents. */
    pressed_zone=K_OTHER; centre_pending=false; release_seen=true;
    clock_start(); return true;
}
void device_display_off(void) { clock_stop(); lcd_init(SCR_TYPE_INVALID); }
void device_blit(uint16_t *frame) { lcd_blit(frame,SCR_320x240_565); }
void device_message(const char *s) { show_msgbox("Folio",s); }

static const char *without_slashes(const char *s) { while (*s=='/') s++; return s; }
int main(int argc,char **argv) {
    assert_ndless_rev(2004);
    const char *docs=get_documents_dir(); char root[F_PATH],expected[F_PATH];
    if (!docs || !join_path(root,docs,"folio") || !join_path(expected,root,"Folio.tns")) return 1;
    if (!argc || !argv[0] || strcasecmp(without_slashes(argv[0]),without_slashes(expected))) {
        device_message("Place Folio.tns in the top-level folio folder in My Documents.");
        return 1;
    }
    return folio_run(argv[0]);
}
