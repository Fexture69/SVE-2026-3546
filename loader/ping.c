/*
 * Minimal entry probe: proves the exploit's control-flow hijack reached our
 * code, with no bundle parsing, LZMA, SHA-256 or flash access in the way.
 *
 * If the reply comes back RESULT_OK the entry path (out_base / WALK_LEN /
 * send-callback global / CODE_ADDRESS) is correct and any later failure is
 * inside the payload logic.  If it does not, the hijack never happened.
 */
#include "../loader_profile.h"

typedef unsigned int u32;
typedef unsigned long u64;

#define send_response ((u32 (*)(void *, u32))SEND_CALLBACK_DEFAULT)

static void cache(void *pointer, u32 size)
{
    u64 ctr;
    __asm__ volatile("mrs %0, ctr_el0" : "=r"(ctr));
    u64 line = 4ULL << ((ctr >> 16) & 15);
    u64 end = (u64)pointer + size;
    u64 address = (u64)pointer & -line;
    while (address < end) {
        __asm__ volatile("dc civac, %0" : : "r"(address) : "memory");
        address += line;
    }
    __asm__ volatile("dsb sy\nisb" : : : "memory");
}

__attribute__((used)) u32 start_impl(void *response, u32 response_size)
{
    *(volatile u64 *)SEND_CALLBACK = SEND_CALLBACK_DEFAULT;
    ((u32 *)response)[1] = RESULT_OK;
    cache(response, response_size);
    return send_response(response, response_size);
}
