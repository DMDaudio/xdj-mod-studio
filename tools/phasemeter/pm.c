/* pm.c -- Type 1 phase meter for the XDJ-700 (XDJ-1000MK2 style), deck C.
 *
 * Replaces the stock 4-box meter rows above the centre waveform with two
 * scrolling beat rulers: orange = master (needs link data: dim line for
 * now), blue = this deck. Grey ticks = beats, red = downbeats, +-4 beats
 * around a fixed white "now" line at the waveform playhead (x 366).
 *
 * Called from pmhook.S on every 2DG flush (FUN_08E7D278(r4 dev, r5 req)).
 * Draws only when:
 *   - the request is the full-screen UI quad (same test as perform_pads),
 *   - the main screen is up (mode word [0x0BA371D4] == 1),
 *   - MODS are on and PM_MODE == 1,
 *   - the meter is actually visible (stock box border pixel, or our own
 *     sentinel pixel) -> an open Browse list / popup is never painted over.
 * Data (see CLAUDE.md §65): beat grid = ANLZ ctx [0x09826BE4] + 0xD7C
 * ("PQTZ", count @+0x14, ptr @+0x18; LE {u16 beat-in-bar, u16 bpm*100,
 * u32 ms}), snapshotted when the loaded track changes (browsing reuses
 * the ctx); position = [0x0C0E6D1C] (POSL, 150 Hz -- the time digits'
 * own source, live on a real deck; the old 0x09D246E0 word freezes
 * on hardware: CLAUDE.md sec. 103/104).
 *
 * Layer writes go through the uncached P2 alias (the 2DG engine reads RAM).
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

#define R32(a) (*(volatile u32 *)(a))
#define R16(a) (*(volatile u16 *)(a))
#define R8(a)  (*(volatile u8 *)(a))
#define UNCACHED(p) (((u32)(p) & 0x1FFFFFFFu) | 0xA0000000u)

#define MODEW      0x0BA371D4u   /* 1 main, 2 browse, 7 PERFORM */
#define CTX_TAB    0x09826BE4u
#define PQTZ_OFF   0xD7Cu
#define TAG_PQTZ   0x5A545150u   /* "PQTZ" as stored */
/* position: POSL (150/s) is what the time digits show -- derived from the
 * DSP stream copy and LIVE on a real deck. The word we used first,
 * [0x09D246E0] (75 Hz), freezes at the load position on real silicon
 * (CLAUDE.md sec. 103/104: healthy real-DSP play, clock still moving), so
 * the meter's beats never scrolled on hardware. POSL @0x0C0E6D1C = 2x that
 * word's units and keeps moving both in the HLE emulator (driven via it)
 * and with the real DSP program. */
#define POSL       0x0C0E6D1Cu   /* 150 Hz */
#define PREVIEW    0x0BA3BCCCu   /* loaded track's overview (changes on load) */
#ifndef MSOFF
#define MSOFF      0x081DBDF1u   /* MY SETTINGS: 1 = MODS OFF */
#endif

/* geometry (measured on the emulated deck) */
#define X0 260
#define X1 471
#define CX 366
#define OY 124                   /* orange (master) line */
#define BY 150                   /* blue (this deck) line */
#define TOPY 116
#define BOTY 158

#define RGB(r, g, b) (u16)((((r) >> 3) << 11) | (((g) >> 2) << 5) | ((b) >> 3))
#define C_OR   RGB(232, 141, 33)
#define C_ORD  RGB(90, 55, 15)
#define C_BL   RGB(36, 139, 228)
#define C_BLD  RGB(15, 55, 90)
#define C_TICK RGB(150, 150, 150)
#define C_RED  RGB(226, 40, 40)
#define C_WH   RGB(255, 255, 255)
#define SENTINEL 0x0821          /* near-black marker we own */
#define STOCK_BORDER 0x89E0      /* stock orange box border at (260,120) */

#define MAXBEATS 4096            /* ~34 min at 120 BPM */

/* ---- state (in the cave's bss; the installer zero-fills it) ---- */
volatile u8 pm_mode;             /* 0 = stock meter, 1 = Type 1 */
static u32 pm_hash;
static u32 pm_n;
static u32 pm_ms[MAXBEATS];
static u8 pm_bar[MAXBEATS];      /* beat-in-bar 1..4 */
u32 pm_draws;                    /* frames drawn (test/diagnostic) */
u32 pm_layer;                    /* layer drawn last (test: buffers alternate) */

#include "pm_label.h"            /* LABEL_W, LABEL_H, label_alpha[] (0..32) */

static u32 hash_preview(void)
{
    u32 h = 2166136261u;
    const u32 *p = (const u32 *)PREVIEW;
    for (int i = 0; i < 100; i++) h = (h ^ p[i]) * 16777619u;
    return h;
}

static void snapshot_grid(void)
{
    u32 ctx = R32(CTX_TAB);
    pm_n = 0;
    if (!ctx || R32(ctx + PQTZ_OFF) != TAG_PQTZ || R32(ctx + PQTZ_OFF + 0x10) != 8)
        return;
    u32 n = R32(ctx + PQTZ_OFF + 0x14);
    const u8 *e = (const u8 *)R32(ctx + PQTZ_OFF + 0x18);
    if (!e || n < 2) return;
    if (n > MAXBEATS) n = MAXBEATS;
    for (u32 i = 0; i < n; i++, e += 8) {
        pm_bar[i] = e[0];
        pm_ms[i] = e[4] | e[5] << 8 | e[6] << 16 | (u32)e[7] << 24;
    }
    pm_n = n;
}

static inline void put(u16 *lay, int stride, int x, int y, u16 c)
{
    lay[y * stride + x] = c;
}

static void vline(u16 *lay, int stride, int x, int y0, int y1, u16 c)
{
    if (x < X0 || x > X1) return;
    for (int y = y0; y <= y1; y++) put(lay, stride, x, y, c);
}

static u16 blend(u16 c, int a)   /* c * a/32 over black */
{
    int r = (c >> 11) * a >> 5, g = ((c >> 5) & 63) * a >> 5, b = (c & 31) * a >> 5;
    return (u16)(r << 11 | g << 5 | b);
}

/* current beat index i and fraction (0..255) of the way to beat i+1 */
static int beat_at(u32 ms, int *frac)
{
    if (pm_n < 2 || ms < pm_ms[0]) return -1;
    int lo = 0, hi = (int)pm_n - 1;
    if (ms >= pm_ms[hi]) { *frac = 0; return hi; }
    while (hi - lo > 1) {             /* pm_ms[lo] <= ms < pm_ms[hi] */
        int mid = (lo + hi) >> 1;
        if (pm_ms[mid] <= ms) lo = mid; else hi = mid;
    }
    u32 span = pm_ms[lo + 1] - pm_ms[lo];
    *frac = span ? (int)(((ms - pm_ms[lo]) << 8) / span) : 0;
    return lo;
}

void pm_draw(u16 *lay, int stride)
{
    /* the row lines (full width), dim when the row has no data */
    int i = -1, frac = 0;
    u32 ms = R32(POSL) * 20u / 3u;              /* 1/150 s -> ms */
    if (pm_n) i = beat_at(ms, &frac);
    for (int y = TOPY; y <= BOTY; y++)
        for (int x = X0 - 2; x <= X1 + 2; x++) put(lay, stride, x, y, 0);
    for (int x = X0; x <= X1; x++) {
        put(lay, stride, x, OY, C_ORD); put(lay, stride, x, OY + 1, C_ORD);
        u16 b = i >= 0 ? C_BL : C_BLD;
        put(lay, stride, x, BY, b); put(lay, stride, x, BY + 1, b);
    }
    if (i >= 0) {
        for (int k = -5; k <= 5; k++) {
            int j = i + k;
            if (j < 0 || j >= (int)pm_n) continue;
            /* 211 px for 8 beats */
            int x = CX + (((k << 8) - frac) * (X1 - X0)) / (8 << 8);
            if (pm_bar[j] == 1) {             /* downbeat: red, crosses line */
                vline(lay, stride, x - 1, BY - 8, BY + 9, C_RED);
                vline(lay, stride, x, BY - 8, BY + 9, C_RED);
            } else {
                vline(lay, stride, x, BY - 8, BY - 2, C_TICK);
            }
        }
    }
    for (int y = TOPY - 1; y <= BOTY + 1; y++)       /* now */
        for (int x = CX - 1; x <= CX + 1; x++) put(lay, stride, x, y, C_WH);
    for (int y = 0; y < LABEL_H; y++)                /* MASTER CLOCK */
        for (int x = 0; x < LABEL_W; x++) {
            int a = label_alpha[y * LABEL_W + x];
            if (a) put(lay, stride, LABEL_X + x, LABEL_Y + y, blend(C_OR, a));
        }
    put(lay, stride, X0 - 2, TOPY, SENTINEL);
    pm_draws++;
}

/* r4 = dev, r5 = req (FUN_08E7D278 args); returns nothing, never blocks */
void pm_hook(u32 dev, u32 req)
{
    (void)dev;
    if (pm_mode != 1 || R8(MSOFF) || R32(MODEW) != 1) return;
    const u32 *w = (const u32 *)R32(req);              /* display list */
    if (!w || w[13] != 0x4C || w[16] != 0x58) return;
    if ((w[27] >> 24) != 0x82 || w[29] != 0x032001E0u) return;
    u16 *lay = (u16 *)UNCACHED(w[14]);
    int stride = (int)w[17];
    if (stride < 800 || stride > 1024) return;
    u16 b = lay[120 * stride + 260], s = lay[TOPY * stride + X0 - 2];
    if (b != STOCK_BORDER && s != SENTINEL) return;   /* meter not visible */
    u32 h = hash_preview();
    if (h != pm_hash) { pm_hash = h; snapshot_grid(); }
    pm_draw(lay, stride);
    pm_layer = w[14];
}
