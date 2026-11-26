#include "folio.h"
#include "codec.h"
#include "miniz_tinfl.h"
#include "LzmaDec.h"
#include <stdlib.h>
#include <string.h>

#define STB_IMAGE_IMPLEMENTATION
#define STBI_ONLY_JPEG
#define STBI_NO_STDIO
#define STBI_NO_SIMD
#define STBI_NO_FAILURE_STRINGS
#define STBI_MAX_DIMENSIONS 640
#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-parameter"
#endif
#include "stb_image.h"
#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

struct Decoder {
    tinfl_decompressor inflate;
    uint8_t *atlas;
    uint32_t glyph_count;
    uint8_t scratch[2 * F_WIDTH * F_BAND];
    uint16_t gray[16];
};

static uint16_t rgb565(unsigned r, unsigned g, unsigned b) {
    return (uint16_t)(((r & 248) << 8) | ((g & 252) << 3) | (b >> 3));
}

bool decode_cover(const uint8_t *src,size_t size,uint16_t *dst) {
    int w,h,n;
    if (!size || size>32768 || !stbi_info_from_memory(src,(int)size,&w,&h,&n) || w!=F_COVER_W || h!=F_COVER_H) return false;
    uint8_t *rgb=stbi_load_from_memory(src,(int)size,&w,&h,&n,3);
    if (!rgb) return false;
    for (unsigned i=0;i<F_COVER_W*F_COVER_H;i++) dst[i]=rgb565(rgb[3*i],rgb[3*i+1],rgb[3*i+2]);
    stbi_image_free(rgb); return true;
}

Decoder *decoder_new(void) {
    Decoder *d = calloc(1, sizeof(*d));
    if (d) for (unsigned i = 0; i < 16; i++) d->gray[i] = rgb565(i * 17, i * 17, i * 17);
    return d;
}

void decoder_free(Decoder *d) { if (d) free(d->atlas); free(d); }

static bool inflate_into(Decoder *d, const uint8_t *data, size_t length, uint8_t *out, size_t expected) {
    size_t consumed = length, written = expected;
    tinfl_init(&d->inflate);
    tinfl_status result = tinfl_decompress(&d->inflate, data, &consumed, out,
        out, &written, TINFL_FLAG_PARSE_ZLIB_HEADER | TINFL_FLAG_USING_NON_WRAPPING_OUTPUT_BUF);
    return result == TINFL_STATUS_DONE && consumed == length && written == expected;
}

static uint32_t u32le(const uint8_t *p) {
    return p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}

static void *atlas_alloc(ISzAllocPtr allocator, size_t n) {
    (void)allocator;
    return malloc(n);
}
static void atlas_free(ISzAllocPtr allocator, void *p) {
    (void)allocator;
    free(p);
}
static bool read_lz_atlas(const uint8_t *input, size_t n, uint8_t *output, size_t expected) {
    /* LZMA1 lc=3, lp=0, pb=2, 256 KiB dictionary. Output doubles as its history. */
    static const Byte props[5]={0x5d,0,0,4,0};
    static const ISzAlloc alloc={atlas_alloc,atlas_free};
    SizeT consumed=n, written=expected;
    ELzmaStatus status;
    SRes result=LzmaDecode(output,&written,input,&consumed,props,sizeof(props),LZMA_FINISH_END,&status,&alloc);
    return result==SZ_OK && status==LZMA_STATUS_FINISHED_WITH_MARK && consumed==n && written==expected;
}

static uint32_t atlas_checksum(const uint8_t *p, size_t bytes) {
    uint32_t a=1,b=0;
    while (bytes) {
        size_t chunk=bytes>5552 ? 5552 : bytes;
        bytes-=chunk;
        while (chunk--) { a+=*p++; b+=a; }
        a%=65521; b%=65521;
    }
    return (b<<16)|a;
}

bool decoder_load_atlas(Decoder *d, const uint8_t *data, size_t length) {
    if (length < 5) return false;
    uint32_t tag=u32le(data), size=tag & 0x7fffffffu;
    if (size < 12 || size > 760000) return false;
    d->atlas=malloc(size);
    if (!d->atlas) return false;
    if (tag & 0x80000000u) {
        if (length<9 || !read_lz_atlas(data+8,length-8,d->atlas,size) ||
            atlas_checksum(d->atlas,size)!=u32le(data+4)) return false;
    } else if (!inflate_into(d,data+4,length-4,d->atlas,size)) return false;
    d->glyph_count=u32le(d->atlas);
    if (!d->glyph_count || d->glyph_count>16000) return false;
    uint32_t next=4+4*(d->glyph_count+1);
    if (next>=size) return false;
    for (uint32_t i=0;i<d->glyph_count;i++) {
        uint32_t off=u32le(d->atlas+4+i*4);
        if (off!=next || off+2>size) return false;
        uint32_t w=d->atlas[off],h=d->atlas[off+1];
        if (!w || !h || h>120) return false;
        next+=2+(w+1)/2*h;
        if (next>size) return false;
    }
    return next==size && u32le(d->atlas+4+4*d->glyph_count)==size;
}
static unsigned read16(const uint8_t *p) { return p[0] | ((unsigned)p[1] << 8); }

static bool text_band(Decoder *d,const uint8_t *src,size_t size,unsigned width,uint16_t *dst,unsigned mode) {
    size_t header = mode==11 ? 4u : 2u;
    if (size<=header || !d->atlas) return false;
    unsigned count=read16(src), patch=mode==11 ? read16(src+2) : 0;
    size_t raw=5u*count+patch;
    if (!count || raw>sizeof(d->scratch) || !inflate_into(d,src+header,size-header,d->scratch,raw)) return false;
    memset(dst,0xff,(size_t)width*F_BAND*2);
    int x=0, y=0, right=0, bottom=0;
    for (unsigned i=0;i<count;i++) {
        const uint8_t *v=d->scratch+i;
        unsigned id=mode==3 ? read16(d->scratch+5*i) : v[0]|((unsigned)v[count]<<8);
        if (id>=d->glyph_count) return false;
        const uint8_t *g=d->atlas+u32le(d->atlas+4+id*4);
        unsigned w=g[0],h=g[1],stride=(w+1)/2;
        if (mode==3) {
            x=(int)read16(d->scratch+5*i+2); y=(int8_t)d->scratch[5*i+4];
        } else {
            unsigned dx=v[2*count]|((unsigned)v[3*count]<<8);
            if (mode==11) {
                x=right+(dx&1 ? -(int)(dx/2)-1 : (int)(dx/2));
                y=(int8_t)(bottom+v[4*count]-(int)h);
            } else {
                x=(uint16_t)(x+dx); y=(int8_t)(y+v[4*count]);
            }
        }
        if (x<0 || (unsigned)x+w>width || y>=(int)F_BAND || y+(int)h<=0) return false;
        right=x+(int)w; bottom=y+(int)h;
        int begin=y<0?-y:0, end=bottom>F_BAND?F_BAND-y:(int)h;
        for (int r=begin;r<end;r++) {
            const uint8_t *row=g+2+(unsigned)r*stride;
            uint16_t *out=dst+(unsigned)(y+r)*width+(unsigned)x;
            for (unsigned col=0;col<w;col++) out[col]=d->gray[(row[col/2]>>(col%2?0:4))&15];
        }
    }
    const uint8_t *p=d->scratch+count*5;
    unsigned previous=0;
    while (patch) {
        if (patch<4) return false;
        unsigned at=read16(p), n=read16(p+2);
        if (!n || at<previous || at+n>width*F_BAND || 2*n>patch-4) return false;
        for (unsigned j=0;j<n;j++) dst[at+j]=(uint16_t)read16(p+4+2*j);
        previous=at+n; p+=4+2*n; patch-=4+2*n;
    }
    return true;
}

static bool native_color(Decoder *d,unsigned mode,const uint8_t *src,size_t size,unsigned width,size_t n,uint16_t *dst) {
    if (size<2) return false;
    if (mode == 8) {
        unsigned filter = *src++;
        if (filter>1 || !inflate_into(d,src,size-1,(uint8_t *)dst,n*2)) return false;
        if (filter) for (size_t i=width;i<n;i++) dst[i]^=dst[i-width];
        return true;
    }
    if (mode == 2 || mode == 9) {
        uint16_t palette[256];
        unsigned count = *src++ + (mode==9);
        size--;
        if (!count || (mode==2 && count>32) || size <= 2 * count) return false;
        for (unsigned i = 0; i < count; i++) palette[i] = src[2*i] | ((uint16_t)src[2*i+1] << 8);
        if (!inflate_into(d, src + count * 2, size - count * 2, d->scratch, n)) return false;
        for (size_t i = 0; i < n; i++) {
            if (d->scratch[i] >= count) return false;
            dst[i] = palette[d->scratch[i]];
        }
        return true;
    }
    return false;
}
bool decode_half(Decoder *d,const uint8_t *src,size_t size,uint16_t *dst) {
    return size>=3 && (src[0]==8 || src[0]==9) &&
        native_color(d,src[0],src+1,size-1,F_WIDTH,F_WIDTH*(F_BAND/2),dst);
}

bool decode_band(Decoder *d, const uint8_t *src, size_t size, unsigned width, uint16_t *dst) {
    if (size < 2 || (width != F_WIDTH && width != 2 * F_WIDTH)) return false;
    size_t n = width * F_BAND;
    uint8_t mode = *src++;
    size--;
    if (mode == 0 || mode == 5) {
        if (!inflate_into(d, src, size, d->scratch, n / 2)) return false;
        if (mode==5) {
            size_t stride=width/2;
            for (size_t i=stride;i<n/2;i++) d->scratch[i]^=d->scratch[i-stride];
        }
        for (size_t i = 0; i < n / 2; i++) {
            uint8_t p = d->scratch[i];
            *dst++ = d->gray[p >> 4]; *dst++ = d->gray[p & 15];
        }
        return true;
    }
    if (mode==2 || mode==8 || mode==9) return native_color(d,mode,src,size,width,n,dst);
    if (mode==10 && width==2*F_WIDTH && decode_half(d,src,size,dst)) {
        for (int y=F_BAND/2-1;y>=0;y--) for(int x=F_WIDTH-1;x>=0;x--) {
            uint16_t c=dst[y*F_WIDTH+x];
            uint16_t *p=dst+(unsigned)(y*2)*width+2*x;
            p[0]=p[1]=p[width]=p[width+1]=c;
        }
        return true;
    }
    if (mode == 6) {
        if (!inflate_into(d,src,size,d->scratch,n/4)) return false;
        for (size_t i=0;i<n/4;i++) {
            uint8_t v=d->scratch[i];
            *dst++=d->gray[(v>>6)*5]; *dst++=d->gray[((v>>4)&3)*5];
            *dst++=d->gray[((v>>2)&3)*5]; *dst++=d->gray[(v&3)*5];
        }
        return true;
    }
    if (mode==3 || mode==4 || mode==11) return text_band(d,src,size,width,dst,mode);
    if (mode != 1) return false;
    int w, h, channels;
    if (!stbi_info_from_memory(src, (int)size, &w, &h, &channels) || w != (int)width || h != F_BAND) return false;
    uint8_t *rgb = stbi_load_from_memory(src, (int)size, &w, &h, &channels, 3);
    if (!rgb) return false;
    for (size_t i = 0; i < n; i++) dst[i] = rgb565(rgb[i*3], rgb[i*3+1], rgb[i*3+2]);
    stbi_image_free(rgb);
    return true;
}
