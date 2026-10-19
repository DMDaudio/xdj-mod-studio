/* tsentry.S -- fixed entry table at TS_BASE: +0 ts_push, +4 ts_apply
 * (callers load the address from here, so the C layout can move). */
        .section .entry,"ax"
        .global _ts_table
        .align 2
_ts_table:
        .long   _ts_push
        .long   _ts_apply
