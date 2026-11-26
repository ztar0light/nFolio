#ifndef FOLIO_SHELF_H
#define FOLIO_SHELF_H
#include "folio.h"
typedef struct {
    char *path;
    int parent;
    bool directory;
    BookTitle *title;
    uint32_t saved_page;
} Entry;
typedef struct { Entry *entries; int count,capacity,books; } Shelf;
bool shelf_scan(const char *root,Shelf *s);
void shelf_free(Shelf *s);
const char *entry_name(const Entry *e);
int shelf_children(Shelf *s,int folder,Entry ***out);
#endif
