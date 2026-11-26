#include "folio.h"
#include <stdio.h>
#include <string.h>

#define RECORD 40u
bool join_path(char *out, const char *folder, const char *name) {
    size_t len = strlen(folder);
    int n = snprintf(out, F_PATH, "%s%s%s", folder, len && folder[len-1] != '/' ? "/" : "", name);
    return n >= 0 && n < F_PATH;
}
static bool slot_path(char *path, const char *folder, const uint8_t *id, unsigned slot) {
    char name[56];
    if (id) {
        /* Preserve all 128 identity bits within a 32-character filename. */
        static const char alphabet[]="0123456789abcdefghijklmnopqrstuv";
        unsigned n=1,bits=0; uint32_t acc=0;
        name[0]='m';
        for (unsigned i=0;i<16;i++) {
            acc=(acc<<8)|id[i]; bits+=8;
            while (bits>=5) { bits-=5; name[n++]=alphabet[(acc>>bits)&31]; }
        }
        if (bits) name[n++]=alphabet[(acc<<(5-bits))&31];
        snprintf(name+n,sizeof(name)-n,"%c.tns",'a'+(int)slot);
    } else snprintf(name, sizeof(name), "folio-last-%c.tns", 'a'+(int)slot);
    return join_path(path,folder,name);
}
static uint32_t unpack(const uint8_t *p) {
    return p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}
static void pack(uint8_t *p, uint32_t n) {
    p[0]=(uint8_t)n; p[1]=(uint8_t)(n>>8); p[2]=(uint8_t)(n>>16); p[3]=(uint8_t)(n>>24);
}
uint32_t folio_checksum(const uint8_t *b,unsigned length) {
    uint32_t h=2166136261u;
    for (unsigned i=0; i<length; i++) h=(h^b[i])*16777619u;
    return h;
}
static bool read_slot(const char *folder, const uint8_t *id, unsigned slot, Saved *out) {
    char path[F_PATH]; uint8_t b[RECORD];
    if (!slot_path(path,folder,id,slot)) return false;
    FILE *f=fopen(path,"rb");
    if (!f) return false;
    bool ok=fread(b,1,sizeof(b),f)==sizeof(b);
    fclose(f);
    if (!ok || unpack(b+36)!=folio_checksum(b,RECORD-4) || (id && memcmp(id,b+8,16))) return false;
    /* FJS1 used byte 35 for continuous scrolling. FJS2 adds the trim
       step above that bit and retains both alternating 40-byte slots. */
    if (memcmp(b,"FJS",3) || b[3]<'1' || b[3]>'3') return false;
    bool magnified=b[3]=='3';
    if ((b[3]=='1' && b[35]>1) || (!magnified && (b[34]&SPREAD_DETAIL))) return false;
    if (magnified && ((b[34]&(SPREAD_ON|SPREAD_DETAIL))!=(SPREAD_ON|SPREAD_DETAIL) ||
        b[29]>F_WIDTH/2 || b[30]>F_HEIGHT/2 || b[31])) return false;
    out->sequence=unpack(b+4); memcpy(out->id,b+8,16);
    out->view=(View){.y=unpack(b+24),.x=magnified?b[28]:unpack(b+28),.level=b[32],.hud=b[33],
                     .spread=b[34],.continuous=b[35]&1u,.trim=b[35]>>1,
                     .spread_x=magnified?b[29]-F_WIDTH/4:0,.spread_y=magnified?b[30]-F_HEIGHT/4:0};
    return (out->sequence&1u)==slot && b[32]<2 && b[33]<2 && b[34]<=SPREAD_MASK && out->view.trim<=F_TRIM_MAX;
}
bool save_load(const char *folder, const uint8_t *id, Saved *out) {
    Saved a,b;
    bool has_a=read_slot(folder,id,0,&a), has_b=read_slot(folder,id,1,&b);
    if (!has_a && !has_b) return false;
    *out = has_b && (!has_a || (int32_t)(b.sequence-a.sequence)>0) ? b : a;
    return true;
}
bool save_commit(const char *folder, const uint8_t *book_id, const uint8_t *id, View v) {
    Saved old;
    bool exists=save_load(folder,book_id,&old);
    if (exists && !memcmp(old.id,id,16) && view_equal(old.view,v)) return true;
    uint32_t seq=exists ? old.sequence+1 : 1;
    uint8_t b[RECORD]={0}; char path[F_PATH];
    if (!slot_path(path,folder,book_id,seq&1u)) return false;
    bool magnified=(v.spread&SPREAD_DETAIL)!=0;
    memcpy(b,magnified?"FJS3":v.trim?"FJS2":"FJS1",4); pack(b+4,seq); memcpy(b+8,id,16); pack(b+24,v.y); pack(b+28,v.x);
    /* Single-page horizontal pan fits in byte 28. A magnified pair uses
       the next two bytes for its own independent pan. */
    if (magnified) { b[29]=(uint8_t)(v.spread_x+F_WIDTH/4); b[30]=(uint8_t)(v.spread_y+F_HEIGHT/4); }
    b[32]=(uint8_t)v.level; b[33]=(uint8_t)v.hud; b[34]=(uint8_t)v.spread;
    b[35]=(uint8_t)((v.trim<<1)|v.continuous); pack(b+36,folio_checksum(b,RECORD-4));
    FILE *f=fopen(path,"wb");
    if (!f) return false;
    bool ok=fwrite(b,1,sizeof(b),f)==sizeof(b) && fflush(f)==0;
    return fclose(f)==0 && ok;
}
