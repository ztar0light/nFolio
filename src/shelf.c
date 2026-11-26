#include "shelf.h"
#include <dirent.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

const char *entry_name(const Entry *e) {
    const char *last=strrchr(e->path,'/');
    return last?last+1:e->path;
}
static bool suffix(const char *name) {
    size_t n=strlen(name);
    return n>10 && !strcasecmp(name+n-10,".folio.tns");
}
static bool append(Shelf *s,const char *path,int parent,BookTitle *title,uint32_t page) {
    if (s->count==s->capacity) {
        int cap=s->capacity?s->capacity*2:16;
        Entry *entries=realloc(s->entries,(size_t)cap*sizeof(*entries));
        if (!entries) return false;
        s->entries=entries; s->capacity=cap;
    }
    char *copy=malloc(strlen(path)+1);
    if (!copy) return false;
    strcpy(copy,path);
    s->entries[s->count++]=(Entry){copy,parent,title==NULL,title,page};
    if (title) s->books++;
    return true;
}
bool shelf_scan(const char *root,Shelf *s) {
    char path[F_PATH];
    /* Append folders to the same worklist. Close each directory before
       enumerating its children to keep stack and open handles bounded. */
    for (int parent=-1;parent<s->count;parent++) {
        if (parent>=0 && !s->entries[parent].directory) continue;
        const char *folder=parent<0?root:s->entries[parent].path;
        DIR *dir=opendir(folder);
        if (!dir) { if (parent<0) return false; continue; }
        struct dirent *item; bool ok=true;
        while (ok && (item=readdir(dir))) {
            if (item->d_name[0]=='.' || !join_path(path,folder,item->d_name)) continue;
            if (parent<0 && !strcasecmp(item->d_name,"background.folio.tns")) continue;
            if (suffix(item->d_name)) {
                BookTitle *title=malloc(sizeof(*title));
                if (!title) { ok=false; break; }
                if (!book_peek(path,title)) { free(title); continue; }
                Saved old; uint32_t page=0;
                bool saved=save_load(root,title->id,&old);
                if (!saved && parent>=0 && save_load(folder,title->id,&old)) {
                    /* A previous installation can be moved into a section intact. */
                    saved=save_commit(root,title->id,title->id,old.view);
                }
                if (saved) book_saved_page(path,old.view,&page);
                ok=append(s,path,parent,title,page);
                if (!ok) free(title);
            } else {
                size_t n=strlen(item->d_name);
                if (n>=4 && !strcasecmp(item->d_name+n-4,".tns")) continue;
                DIR *child=opendir(path);
                if (child) { closedir(child); ok=append(s,path,parent,NULL,0); }
            }
        }
        closedir(dir);
        if (!ok) return false;
    }
    return true;
}
void shelf_free(Shelf *s) {
    for (int i=0;i<s->count;i++) { free(s->entries[i].title); free(s->entries[i].path); }
    free(s->entries);
}
static int compare(const void *a,const void *b) {
    const Entry *x=*(Entry *const *)a,*y=*(Entry *const *)b;
    if (x->directory!=y->directory) return x->directory?-1:1;
    return strcasecmp(entry_name(x),entry_name(y));
}
int shelf_children(Shelf *s,int folder,Entry ***out) {
    int count=0;
    for (int i=0;i<s->count;i++) if (s->entries[i].parent==folder) count++;
    *out=NULL;
    if (!count) return 0;
    Entry **list=malloc((size_t)count*sizeof(*list));
    if (!list) return -1;
    int j=0;
    for (int i=0;i<s->count;i++) if (s->entries[i].parent==folder) list[j++]=&s->entries[i];
    qsort(list,count,sizeof(*list),compare);
    *out=list; return count;
}
