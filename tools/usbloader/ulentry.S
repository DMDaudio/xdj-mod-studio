/* ulentry.S -- fixed header at UL_BASE: +0 "ULD1", +4 ul_open (the
 * function the hooked open pool word points at), +8 ul_state. */
        .section .entry,"ax"
        .global _ul_head
        .align 2
_ul_head:
        .long   0x31444C55
        .long   _ul_open
        .long   _ul_state
        .long   0
