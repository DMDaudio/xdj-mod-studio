/* ks.c -- REAL-TIME KEY SHIFT for the XDJ-700's audio chip (TI C674x DSP).
 *
 * Runs inside Pioneer's own DSP program, in place of one call. With MASTER
 * TEMPO on, the DSP's time-stretcher pulls decoded frames from a 32-frame
 * staging buffer through a "block puller" (fw 1.15: 0xC001CBF4, called once
 * per 16-sample output block from 0xC0008D64). We wrap that call:
 *
 *   1. resample the staging frames by k (cubic) -> the puller sees audio
 *      whose pitch is k times higher,
 *   2. run the stretcher at speed/k for the whole 512-sample chunk, so it
 *      consumes the resampled frames at the right rate,
 *   3. put the original frames back and report how many ORIGINAL frames
 *      were used (phase-accurate), so the decoder, the play position and
 *      the waveform all stay exactly where they would be.
 * Result: pitch x k, tempo unchanged.
 *
 * k comes from mailbox word 0x11837BD8 (the SH writes it; stock firmware
 * puts a copy of the tempo there, which the DSP does not use for audio):
 *   (w & 0xC0000000) == 0x40000000  ->  k = (w & 0x3FFFFFFF) / 2^20
 *   anything else                   ->  stock behaviour, untouched.
 * k is latched at chunk starts only (the puller fixes the chunk's frame
 * budget there) and only when the staging buffer can feed it; otherwise
 * the chunk runs stock. Linked right after the DSP image (loader padding).
 */
typedef unsigned int u32;

#define B14          0x11806900u
#define SPEED        (*(volatile u32 *)(B14 + 316))          /* Q20 tempo   */
#define BLOCKNO      (*(volatile unsigned short *)(B14 + 510))  /* 0..31    */
#define MAILBOX_BD8  (*(volatile u32 *)0x11837BD8u)
#define STAGE        ((float *)0x1182AD70u)   /* 32 frame slots L,R */
/* 2026-10-25: the 24 bytes before STAGE are NOT history samples but the
 * buffer's bookkeeping record (blk1 0x17414: a11 = 0x1182AD58, passed to the
 * advance routine 0xC00056D0): +0 capacity (32), +4 VALID frames (16 in
 * play), +8 read pos ... The old code read +16/+20 as the "previous" L/R
 * sample and peeked/consumed past the valid frames (the advance routine
 * then refuses to move at all) -- per-block clicks, a buzz on the deck
 * (right channel on AIFF). Now: own history, never past VALID. */
#define STAGE_VALID  (*(volatile u32 *)0x1182AD5Cu)
#define PULLER       ((int (*)(void))0xC001CBF4u)

#define NFR 32

/* v2 2026-10-18 (deck: right-channel noise whenever we WROTE the deck's
 * staging buffer, even identical values; a dry run with the same work was
 * clean, the emulator bit-exact). So we never write staging any more: the
 * stretcher's two staging loads (fw 1.15: mvk/mvkh pairs at 0xC001CD40/
 * CD98 and 0xC001CE34/CE48, both `lddw *aN++` from 0x1182AD70) are
 * re-pointed by the installer (ksh.c) at KS.pbuf. Every block we fill
 * pbuf from staging: a plain copy when not shifting, the k-resampled
 * frames when shifting. Staging is only READ (invalidate first so we see
 * what DMA put there). */
#define L1DWIBAR (*(volatile u32 *)0x01844030u)
#define L1DWIWC  (*(volatile u32 *)0x01844034u)
static void stage_inv(void)
{
    L1DWIBAR = (u32)(STAGE - 8);              /* history + 32 frames */
    L1DWIWC = (NFR * 2 + 8);                  /* in 32-bit words */
    while (L1DWIWC) ;
}

struct ks {
    float pbuf[NFR * 2];   /* stretcher input (8-byte aligned: lddw) */
    u32 magic;
    u32 calls, shifted;
    u32 w_latched;
    float k, phi;
    u32 last_nx, last_ny;
    float hl, hr;          /* the original frame just before STAGE[0] */
    u32 clamps;            /* blocks where VALID limited the take */
};
extern struct ks KS;    /* placed by the linker script */

static float recip(float k)                 /* 1/k, k in [0.25, 4] */
{
    float r = 1.0f;
    if (k > 1.5f) r = 0.5f;
    if (k > 3.0f) r = 0.25f;
    if (k < 0.75f) r = 2.0f;
    if (k < 0.375f) r = 4.0f;
    for (int i = 0; i < 6; i++)
        r = r * (2.0f - k * r);
    return r;
}

static inline float cubic(float xm1, float x0, float x1, float x2, float t)
{   /* Catmull-Rom */
    float a = -0.5f * xm1 + 1.5f * x0 - 1.5f * x1 + 0.5f * x2;
    float b = xm1 - 2.5f * x0 + 2.0f * x1 - 0.5f * x2;
    float c = -0.5f * xm1 + 0.5f * x1;
    return ((a * t + b) * t + c) * t + x0;
}

int ks_pull(void)
{
    struct ks *s = &KS;
    const float *x = STAGE;
    float *y = s->pbuf;
    if (s->magic != 0x4B534833u) {           /* v3 layout */
        s->magic = 0x4B534833u; s->w_latched = 0; s->phi = 0.0f;
        s->calls = s->shifted = s->clamps = 0;
        s->hl = s->hr = 0.0f;
    }
    s->calls++;
    stage_inv();
    u32 valid = STAGE_VALID;
    if (valid > NFR) valid = NFR;

    if (BLOCKNO == 0) {                      /* chunk start: (re)latch k */
        u32 w = MAILBOX_BD8;
        float k = (float)(w & 0x3FFFFFFFu) * (1.0f / 1048576.0f);
        float sp = (float)SPEED * (1.0f / 1048576.0f);
        /* staging must cover the chunk's per-block take:
           ceil(16*sp/k)+1 resampled frames from <= 32 originals */
        if ((w & 0xC0000000u) == 0x40000000u && k > 0.5f && k < 2.0f &&
            sp > 0.0f && (16.0f * sp * recip(k) + 2.0f) * k + 3.0f < (float)NFR &&
            16.0f * sp * recip(k) + 2.0f < (float)NFR) {
            if (s->w_latched == 0) s->phi = 0.0f;
            s->w_latched = w; s->k = k;
        } else {
            s->w_latched = 0;
        }
    }
    if (s->w_latched == 0) {                 /* stock: the stretcher sees */
        for (int i = 0; i < NFR * 2; i++)    /* exactly what staging holds */
            y[i] = x[i];
        int used = PULLER();
        if (used > 0 && used <= (int)NFR) {  /* history for a later shift */
            s->hl = x[2 * used - 2]; s->hr = x[2 * used - 1];
        }
        return used;
    }
    if (valid < 4) {                         /* nothing sensible to shift */
        for (int i = 0; i < NFR * 2; i++) y[i] = x[i];
        return PULLER();
    }

    /* 1. resampled frames -> pbuf (staging untouched) */
    float k = s->k, phi = s->phi;
    int last = 0;
    int vl = (int)valid - 1;                 /* last valid frame index */
    for (int j = 0; j < NFR; j++) {
        float pos = phi + (float)j * k;
        int i = (int)pos;                    /* pos >= 0 */
        float t = pos - (float)i;
        if (i > vl) break;                   /* past the valid frames */
        int i1 = i + 1 > vl ? vl : i + 1, i2 = i + 2 > vl ? vl : i + 2;
        float lm1 = i ? x[2 * i - 2] : s->hl, rm1 = i ? x[2 * i - 1] : s->hr;
        y[2 * j]     = cubic(lm1, x[2 * i], x[2 * i1], x[2 * i2], t);
        y[2 * j + 1] = cubic(rm1, x[2 * i + 1], x[2 * i1 + 1], x[2 * i2 + 1], t);
        last = j;
    }
    for (int j = last + 1; j < NFR; j++) {   /* never consumed; keep sane */
        y[2 * j] = y[2 * last]; y[2 * j + 1] = y[2 * last + 1];
    }

    /* 2. stretcher at speed/k (the puller reads SPEED at block 0 only) */
    u32 sp0 = SPEED, sp1 = (u32)((float)sp0 * recip(k) + 0.5f);
    SPEED = sp1;
    int ny = PULLER();
    if (SPEED == sp1) SPEED = sp0;           /* host may have updated it */

    /* 3. report original frames used -- never more than are VALID (the
     * advance routine ignores a block that over-consumes); the rest of the
     * position carries in phi to the next block */
    float end = phi + (float)ny * k;
    int nx = (int)end;
    if (nx > (int)valid) { nx = (int)valid; s->clamps++; }
    s->phi = end - (float)nx;
    if (s->phi > 8.0f) s->phi = 8.0f;        /* never run away */
    if (nx > 0) { s->hl = x[2 * nx - 2]; s->hr = x[2 * nx - 1]; }
    s->shifted++;
    s->last_nx = nx; s->last_ny = ny;
    return nx;
}
