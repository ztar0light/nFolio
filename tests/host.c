/* Drives the same reader loop as the calculator; scripts supply keys and time. */
#include "folio.h"
#include "device.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static FILE *events;
static unsigned repeats;
static Key active;
static uint16_t display[F_WIDTH*F_HEIGHT];
static const char *key_names[]={"none","power","esc","enter","up","down","left","right","next","prev","plus","minus","menu","books","tab","zoom","save","spread","order","pair","wide","center","other","multiply","divide"};
static void snapshot(const char *name,const uint16_t *pixels) {
    FILE *f=fopen(name,"wb");
    if (!f || fwrite(pixels,sizeof(*pixels),F_WIDTH*F_HEIGHT,f)!=F_WIDTH*F_HEIGHT || fclose(f)) exit(91);
}
static Pad current_pad;
Key device_poll(Pad *pad) {
    *pad=current_pad;
    if (repeats) { repeats--; return active; }
    char line[800],token[24],arg[700];
    while (fgets(line,sizeof(line),events)) {
        int n=sscanf(line,"%23s %699s",token,arg);
        if (n<1 || *token=='#') continue;
        if (!strcmp(token,"snap") && n==2) { snapshot(arg,display); continue; }
        if (!strcmp(token,"cut")) exit(73);
        bool ctrl=!strncmp(token,"ctrl",4);
        const char *action=ctrl?token+4:token;
        if (!*action) action="none";
        int x,y;
        if (!strcmp(action,"touch") || !strcmp(action,"click")) {
            if (sscanf(line,"%*s %d %d",&x,&y)!=2) exit(92);
            bool pressed=!strcmp(action,"click");
            *pad=current_pad=(Pad){.x=x,.y=y,.contact=true,.pressed=pressed,.ctrl=ctrl};
            return pressed?K_CENTER:K_NONE;
        }
        *pad=current_pad=(Pad){.ctrl=ctrl};
        for (unsigned i=0;i<sizeof(key_names)/sizeof(key_names[0]);i++) if (!strcmp(key_names[i],action)) {
            unsigned duration=n==2?(unsigned)strtoul(arg,NULL,10):1;
            if (!duration) exit(92);
            active=(Key)i; repeats=duration-1; return active;
        }
        fprintf(stderr,"Unknown action: %s\n",token); exit(93);
    }
    fprintf(stderr,"Key script ended without exit.\n"); exit(94);
}
static uint32_t elapsed_ms;
uint32_t device_millis(void) { return elapsed_ms; }
void device_sleep(unsigned ms) { elapsed_ms+=ms; }
bool device_display_on(void) { return true; }
void device_display_off(void) {}
void device_message(const char *s) { fprintf(stderr,"Folio: %s\n",s); }
void device_blit(uint16_t *f) { memcpy(display,f,sizeof(display)); }
int main(int argc,char **argv) {
    if (argc==4 && !strcmp(argv[1],"run")) {
        events=fopen(argv[3],"r"); if(!events) return 2;
        int result=folio_run(argv[2]); fclose(events); return result;
    }
    if (argc>=7 && !strcmp(argv[1],"render")) {
        const char *err; Book *b=book_open(argv[2],&err);
        if (!b) { fprintf(stderr,"%s\n",err); return 3; }
        View v={.y=(uint32_t)strtoul(argv[3],NULL,10),.x=(uint32_t)strtoul(argv[5],NULL,10),.level=(unsigned)atoi(argv[4])};
        if (argc>=8) v.trim=(unsigned)strtoul(argv[7],NULL,10);
        view_normalize(b,&v);
        if (!book_render(b,v,display)) { book_close(b); return 4; }
        snapshot(argv[6],display); uint32_t first=book_decodes(b);
        view_scroll(b,&v,8);
        if (!book_render(b,v,display)) { book_close(b); return 4; }
        printf("%u %u\n",(unsigned)first,(unsigned)(book_decodes(b)-first));
        book_close(b); return 0;
    }
    return 2;
}
