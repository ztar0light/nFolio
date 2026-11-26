#ifndef FOLIO_CODEC_H
#define FOLIO_CODEC_H
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
typedef struct Decoder Decoder;
Decoder *decoder_new(void);
void decoder_free(Decoder *d);
bool decoder_load_atlas(Decoder *d, const uint8_t *data, size_t length);
bool decode_cover(const uint8_t *src,size_t size,uint16_t *dst);
bool decode_half(Decoder *d,const uint8_t *src,size_t size,uint16_t *dst);
bool decode_band(Decoder *d, const uint8_t *data, size_t len, unsigned width, uint16_t *pixels);
#endif
