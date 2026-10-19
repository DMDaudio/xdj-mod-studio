/* entry table at SM_BASE: +0 sm_fread (the pool word points here) */
        .section .entry,"ax"
        .global _sm_table
        .align 2
_sm_table:
        .long   _sm_fread
