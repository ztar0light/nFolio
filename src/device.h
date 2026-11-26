#ifndef FOLIO_DEVICE_H
#define FOLIO_DEVICE_H
#include <stdbool.h>
#include <stdint.h>
typedef enum { K_NONE, K_EXIT, K_ESC, K_ENTER, K_UP, K_DOWN, K_LEFT, K_RIGHT,
    K_NEXT, K_PREV, K_PLUS, K_MINUS, K_MENU, K_BOOKS, K_TAB, K_ZOOM, K_SAVE,
    K_SPREAD, K_ORDER, K_PAIR, K_WIDE, K_CENTER, K_OTHER, K_TRIM_IN, K_TRIM_OUT } Key;
typedef struct { int x,y; bool contact,pressed,ctrl,scan_failed; } Pad;
Key device_poll(Pad *pad);
void device_sleep(unsigned ms);
uint32_t device_millis(void);
bool device_display_on(void);
void device_display_off(void);
void device_blit(uint16_t *frame);
void device_message(const char *text);
int folio_run(const char *executable);
#endif
