/*
 * Visible entry probe: prints on the bootloader's own screen the moment our
 * code runs, then replies.  If the control block lands on the send callback
 * at any point (even outside a command context), the phone shows the text.
 */
#include "../loader_profile.h"

typedef unsigned int u32;
typedef unsigned long u64;

#define screen_cursor ((u32 (*)(void))SCREEN_CURSOR)
#define screen_printf \
    ((void (*)(u32, u32, u32, u32, const char *, ...))SCREEN_PRINTF)
#define display_update ((void (*)(void))DISPLAY_UPDATE)
#define send_response ((u32 (*)(void *, u32))SEND_CALLBACK_DEFAULT)

__attribute__((used)) u32 start_impl(void *response, u32 response_size)
{
    *(volatile u64 *)SEND_CALLBACK = SEND_CALLBACK_DEFAULT;
    u32 line = screen_cursor();
    screen_printf(20, line, 0x40FF40, 0, "PACKET HIT");
    screen_printf(20, line + 32, 0x40FF40, 0, "CTL LANDED");
    display_update();
    ((u32 *)response)[1] = RESULT_OK;
    return send_response(response, response_size);
}
