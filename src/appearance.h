#ifndef FOLIO_APPEARANCE_H
#define FOLIO_APPEARANCE_H
#include "shelf.h"

typedef enum { BG_PLAIN, BG_LAST, BG_SELECTED, BG_CUSTOM, BG_COUNT } Background;
typedef enum { THEME_LIGHT, THEME_DARK_MENUS, THEME_DARK_ALL, THEME_COUNT } Theme;
typedef struct { Background background; Theme theme; } Appearance;

void appearance_load(const char *root);
Appearance appearance_get(void);
bool appearance_save(Appearance value);
void appearance_reading(const Entry *entry);
const uint16_t *appearance_preview(const Entry *entry);
const uint16_t *appearance_wall(const Entry *selected);
bool appearance_custom_missing(void);
#endif
