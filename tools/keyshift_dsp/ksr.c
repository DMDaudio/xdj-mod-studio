/* ksr.c -- reload the XDJ-700's DSP (audio chip) ONCE from a patched copy of
 * its own program, so real-time key shift (ks.c) is part of it.
 *
 * Nothing in the original program in RAM is changed. The deck's own DSP
 * boot routine (fw 1.15: 0x08C3777C, normally run once at power-on) reads
 * the program from a source pointer (pool 0x08C37918 = 0xA800E770) for a
 * length (pool 0x08D78AC8 = 0x5F4C0). We:
 *   1. refuse unless a request flag is set, no track is loaded and every
 *      word we rely on is stock,
 *   2. copy the program to DST (uncached), append ks.c's code (it lands in
 *      the last chunk's loader padding, DSP 0xC005F4C0..0xC0060000), and
 *      re-point ONE call in the copy (DSP 0xC0008D64 -> 0xC005F4C0),
 *   3. with interrupts blocked like its power-on caller: point the two
 *      pools at the copy, hold the DSP in reset briefly, run the boot
 *      routine, put both pools back,
 *   4. unhook ourselves from the display-flush pool (one-shot).
 * Status (ksr_status): 1 ok; 2 track loaded; 3 not stock; 4 copy verify
 * failed; 0x100+r boot routine returned r (DSP left as the routine left it).
 *
 * 2026-10-17 DIAG (deck returned 0x101 = r=1 '3 tries, no completion'):
 *  - call context matches the routine's power-on caller: SR |= BL AND
 *    IMASK=15 (was IMASK-only),
 *  - ksr_dbg: PLAIN snapshot reads only (no bus spin loops -- the first
 *    diag build's stock DSP-word accessor 0x08C3771A proved to be an async
 *    HPI-request enqueuer whose busy path wedged the emulated deck, so it
 *    is not used anywhere),
 *  - dbg[0]  = HPIC  (uncached 0xB8000000)      pre-boot
 *    dbg[1]  = HPIA  (uncached 0xB8040000)      pre-boot
 *    dbg[2..9]  = SH mailbox ring 0x0C8440..0x0C845B (8 words) pre-boot
 *    dbg[10] = BOOT return code r
 *    dbg[11] = HPIC  post-boot
 *    dbg[12] = HPIA  post-boot
 *    dbg[13..20] = mailbox ring post-boot (8 words)
 *    dbg[21] = packed pin bytes: low byte = 0xA405016C (bit0 = DSP HINT,
 *              0 = asserted), high byte = 0xA405012E (bit0 = DSP out of
 *              reset)
 * dbg[22..23] spare. The ring tells us whether the DSP's firmware ever
 * serviced requests; HPIC/HPIA show where the boot's last HPI transaction
 * stopped; HINT low = the DSP's pipe service was signaling.
 *
 * 2026-10-17 v3 DUAL MODE (split 'environment vs patch' on the deck):
 *  ksr_flag 'KSR1' (0x4B535231) = PATCHED mode as before (append ks.c,
 *  re-point the one call).
 *  ksr_flag 'KSR0' (0x4B535230) = STOCK mode: copy the program UNCHANGED
 *  (no append, no call patch, POOL_LEN stays IMG_LEN). If the deck reloads
 *  from the stock copy, the boot environment is healthy and the patch
 *  content is what the real chip rejects; if even the stock copy fails,
 *  the environment is the problem. Audio returns on success (no power
 *  cycle needed).
 *
 * 2026-10-17 v4 UNMASKED (deck verdict: STOCK copy also fails 0x101 with
 *  the exact signature of 'no servicing happens during the wait', while
 *  both masked contexts (IMASK-only, IMASK+BL) failed identically and the
 *  emulator passes because its model services everything instantly).
 *  v4 drops set_sr entirely: the boot routine runs with the interrupts
 *  EXACTLY as the deck has them, matching how its own audio pipe gets
 *  serviced mid-session. Reset hold kept (cave clear bit0 + ~4 ms).
 *
 * 2026-10-17 v5 RESULT OF v4 (emulator): UNMASKED now REPRODUCES the
 *  deck's 0x101 in the emulator (status after ~950M steps, copy
 *  untouched) -- the servicing question is real and interrupt-blocking
 *  is the differing lever. v5 = MASKED context again (v3 behaviour: the
 *  only context whose emulator rehearsal passes) + the SR proof: dbg[22]
 *  = SR before the mask, dbg[23] = SR after set_sr (did IMASK=15 ever
 *  stick on the real deck?).
 *
 * 2026-10-17 v6: DECK SR PROOF says IMASK=15 DID stick (0x40000001 ->
 *  0x500000F1) and the boot still failed -- so mask+BL is NOT the fix
 *  either. v6 makes the remaining knobs selectable BY FLAG BITS so all
 *  combos can be armed from ONE install (no reboots between attempts):
 *    flag & 0x100 = run UNMASKED (leave SR as the deck has it)
 *    flag & 0x200 = 2-second reset hold (cold-boot-like, was ~4 ms)
 *    base 'KSR1' (0x4B535231) = patched, 'KSR0' (0x4B535230) = stock.
 */
typedef unsigned int u32;
typedef unsigned char u8;

#define POOL_FLUSH   0x08C92E20u     /* display flush hook pool (us)      */
#define POOL_LEN     0x08D78AC8u     /* DSP program length                */
#define POOL_SRC     0x08C37918u     /* DSP program source (uncached)     */
#define IMG          0x0800E770u     /* DSP program in SH RAM             */
#define IMG_LEN      0x0005F4C0u
#define CALL_OFF     0x00008D64u     /* DSP 0xC0008D64: b .S1 0xC001CBF4  */
#define CALL_OLD     0x0027D290u
#define TRACK_LEN    0x0BA3C050u     /* loaded track length (0 = none)    */
#define RESET_PORT   0xA405012Eu     /* bit0 = DSP out of reset           */
#define PINPORT      0xA405016Cu     /* bit0 = DSP HINT (see dspdev.py)   */
#define BOOT         0x08C3777Cu
#define DST          0x097A0000u     /* copy (deck-sampled free region)   */
#define UNC          0xA0000000u
#define HPIC_P2      0xB8000000u     /* uncached CS6: HPIC                */
#define HPIA_P2      0xB8040000u     /* uncached CS6: HPIA                */
#define PIPE_BASE    0x0C8440u       /* SH-side DSP pipe ring words       */
#define PIPE_WORDS   8

#define R32(a) (*(volatile u32 *)(a))
#define R8(a) (*(volatile u8 *)(a))

extern u32 ksr_prev, ksr_flag, ksr_status;
extern u32 ksr_dbg[24];
extern const u8 ks_dsp[], ks_dsp_end[];
extern void ksr_entry(void);

static u32 get_sr(void) { u32 v; __asm__ volatile("stc sr,%0" : "=r"(v)); return v; }
static void set_sr(u32 v) { __asm__ volatile("ldc %0,sr" : : "r"(v) : "memory"); }

static void snap(int i0)
{
    ksr_dbg[i0++] = R32(HPIC_P2);
    ksr_dbg[i0++] = R32(HPIA_P2);
    for (u32 k = 0; k < PIPE_WORDS; k++)
        ksr_dbg[i0++] = R32(PIPE_BASE + 4 * k);
}

void ksr_hook(void)
{
    u32 req;
    int doPatch;
    if (ksr_flag != 0x4B535231u && ksr_flag != 0x4B535230u
        && ksr_flag != 0x4B535331u && ksr_flag != 0x4B535330u
        && ksr_flag != 0x4B535431u && ksr_flag != 0x4B535430u)
        return;                          /* KSR1/KSR0 with 0x100/0x200 bits */
    req = ksr_flag;
    doPatch = (req & 0x1u) != 0u;         /* bit0 of the ASCII digit: 1/0 */
    ksr_flag = 0;
    u32 blen = doPatch ? (u32)(ks_dsp_end - ks_dsp) : 0u;
    u32 nlen = IMG_LEN + ((blen + 3) & ~3u);
    if (R32(TRACK_LEN) != 0) { ksr_status = 2; return; }
    if (R32(POOL_LEN) != IMG_LEN || R32(POOL_SRC) != (IMG | UNC) ||
        R32(IMG + CALL_OFF) != CALL_OLD || nlen > 0x60000u ||
        R32(POOL_FLUSH) != (u32)ksr_entry) { ksr_status = 3; return; }

    /* 2. the copy (uncached: the boot routine reads it uncached) */
    volatile u32 *d = (volatile u32 *)(DST | UNC);
    const u32 *s = (const u32 *)IMG;
    for (u32 i = 0; i < IMG_LEN / 4; i++) d[i] = s[i];
    for (u32 i = 0; i < blen; i += 4) {
        u32 w = ks_dsp[i] | (u32)ks_dsp[i + 1] << 8 | (u32)ks_dsp[i + 2] << 16 |
                (u32)ks_dsp[i + 3] << 24;
        d[(IMG_LEN + i) / 4] = w;
    }
    /* b .S1 disp: cst21 at bits 7..27, relative to the fetch packet */
    if (doPatch) {
        u32 disp = ((IMG_LEN - (CALL_OFF & ~31u)) >> 2) & 0x1FFFFFu;
        d[CALL_OFF / 4] = (CALL_OLD & ~(0x1FFFFFu << 7)) | disp << 7;
    }
    for (u32 i = 0; i < IMG_LEN / 4; i++)
        if (i != CALL_OFF / 4 && d[i] != s[i]) { ksr_status = 4; return; }

    /* 3. reload (v6: context selectable by flag bits) */
    snap(0);                                          /* pre-boot state */
    u32 sr = get_sr();
    ksr_dbg[22] = sr;                                 /* SR before the mask */
    if (!(req & 0x100u))                              /* 0x100 = unmasked */
        set_sr((sr | 0xF0u | 0x10000000u));           /* cold-boot context */
    ksr_dbg[23] = get_sr();                           /* did the mask stick? */
    R32(POOL_SRC) = DST | UNC;
    R32(POOL_LEN) = nlen;
    *(volatile u8 *)RESET_PORT &= (u8)~1u;            /* hold DSP in reset */
    for (volatile u32 n = 0; n < (req & 0x200u ? 250000000u : 400000u); n++) ;
    u32 r = ((u32 (*)(void))BOOT)();
    R32(POOL_LEN) = IMG_LEN;
    R32(POOL_SRC) = IMG | UNC;
    R32(POOL_FLUSH) = ksr_prev;                       /* 4. unhook */
    snap(11);                                         /* post-boot state */
    ksr_dbg[21] = R8(PINPORT);                        /* low byte */
    ksr_dbg[21] |= (u32)R8(RESET_PORT) << 8;
    set_sr(sr);
    ksr_status = r == 0 ? 1u : 0x100u + r;
    ksr_dbg[10] = r;
}
