/* vfile.c -- "virtual WAV" layer: the XDJ-700 plays FLAC and Ogg Opus
 * files as WAVs.
 *
 * Ogg Opus (VF_OPUS): "OggS" + OpusHead (mapping family 0, 1-2 ch) -> a
 * 48 kHz 16-bit WAV. Decoded with libopus' fixed-point decoder (no FPU).
 * Length = last page granule - pre-skip (read from the file tail once).
 * Seeks bisect the pages by granule (target - 80 ms pre-roll, as the Opus
 * spec asks), restart the decoder there and decode forward; the first
 * packet's start sample is the page granule minus the durations of the
 * packets completed on that page (no reliance on the bisection's guess).
 *
 * The deck's media API dispatches every file op by device type through
 * per-type pointer slots (CLAUDE.md §66). We take the USB (type 3) slots:
 *   read  0x08D5FFA8 (media server -> fread) + 0x08DDFFD4 (direct fread)
 *   seek  0x08DE0298   tell 0x08DE04FC   eof 0x08DE0768
 *   size  0x08DE0F48   close 0x08DDFD28
 * For a handle whose file starts with "fLaC" (44.1/48 kHz, 1-2 ch) we own
 * the position and serve a 44-byte RIFF/WAVE header followed by PCM that
 * dr_flac decodes on demand (16-bit, or 24-bit for hi-res sources). Every
 * other file goes straight to the stock functions (o_* = the slot values
 * captured by the installer; vf_read_prev chains vredirect if present).
 * The extension table entry "3g2" is re-pointed to "flac" with the WAV
 * type (0x40B) by the installer, so the firmware runs its WAV parser.
 */
#define DR_FLAC_NO_STDIO
#define DR_FLAC_NO_OGG
#define DR_FLAC_NO_SIMD
#define DR_FLAC_NO_WCHAR
#define DR_FLAC_NO_CRC
#define DR_FLAC_IMPLEMENTATION
#include <stddef.h>
#include <string.h>
#include "dr_flac.h"
#ifdef VF_OPUS
#include "opus.h"
#endif

typedef unsigned char u8;
typedef unsigned int u32;

typedef int (*fread_t)(void *buf, int size, int count, void *fp);
typedef int (*fseek_t)(void *fp, int off, int whence);
typedef int (*ftell_t)(void *fp);
typedef int (*fsize_t)(void *fp, u32 *out);
typedef int (*fclose_t)(void *fp);

/* stock slot values (installer writes them; defaults = fw 1.15 stock) */
#include <stdint.h>
uintptr_t o_fread = 0x08C61A6C, o_seek = 0x08C62E1C, o_tell = 0x08C632F2,
    o_eof = 0x08C63344, o_size = 0x08C71124, o_close = 0x08C605F4,
    vf_read_prev = 0x08C61A6C,     /* pointer-sized: native tests mock them */
    vf_direct_prev = 0x08C61A6C;   /* live direct-read slot before us (live
                                      stems engine, else stock fread) */
u32 vf_stats[8];                  /* 0 probes, 1 flac opens, 2 vreads, 3 decodes,
                                     4 opus opens, 5 opus seeks, 6 opus packets */
/* op log ring (diagnostics): {op, fp, a, b, ret} x 64; op 1 read 2 seek
 * 3 tell 4 eof 5 size 6 close 7 direct-read */
u32 vf_log[64 * 5], vf_logi;
static void L(u32 op, void *fp, u32 a, u32 b, u32 r)
{
    u32 *e = vf_log + (vf_logi++ % 64) * 5;
    e[0] = op; e[1] = (u32)(uintptr_t)fp; e[2] = a; e[3] = b; e[4] = r;
}

#define NSLOT 2
#ifdef VF_OPUS
#define ARENA 0x20000             /* dr_flac state, or opus state + page + pcm */
#else
#define ARENA 0x18000             /* dr_flac state + block buffer */
#endif
#define CACHE_FRAMES 2048
#define NNEG 8

typedef struct {
    void *fp;                     /* deck file handle (0 = free) */
    u32 vpos, vsize, rpos;        /* virtual pos/size, real file pos */
    u32 frames, rate;
    u8 ch, bps, bpf;              /* out bytes per sample / per frame */
    u32 hdrlen;
    u8 hdr[400];                  /* RIFF + fmt + LIST INFO + data hdr */
    char tag[4][64];              /* IART INAM IPRD IGNR from Vorbis comments */
    drflac *f;
    u8 kind;                      /* 1 FLAC, 2 Ogg Opus */
#ifdef VF_OPUS
    OpusDecoder *od;
    u32 fsize, audio_start, pre_skip, gran0;
    u32 pg_off, pg_next, pg_gran, nseg, seg_i, body_i, skip_cont;
    u8 pg_flags, lace[255];
    u8 *pg, *pkt, *scan;          /* page body (64 KB), packet, sync scan */
    u32 pkt_len, pkt_done, smp;   /* smp = granule of the next packet's start */
    short *pcm;                   /* last decoded packet (interleaved) */
    int pcm_start;                /* its first output frame (may be < 0) */
    u32 pcm_n;
#endif
    u32 cache_frame, cache_n, next_frame;
    unsigned char *base;
    size_t used;
    int cache[CACHE_FRAMES * 2];  /* s32 frames from dr_flac */
    unsigned char arena[ARENA];
} vslot;

static vslot S[NSLOT];
static void *neg[NNEG];           /* recent handles known NOT to be FLAC */
static u32 negi;

/* ---- arena for dr_flac (per slot, never freed until close) ---- */
static void *a_malloc(size_t n, void *u)
{
    vslot *s = u;
    n = (n + 15) & ~(size_t)15;
    if (s->used + n + 16 > ARENA) return 0;
    unsigned char *p = s->arena + s->used;
    *(size_t *)p = n;
    s->used += n + 16;
    return p + 16;
}
static void a_free(void *p, void *u) { (void)p; (void)u; }
static void *a_realloc(void *p, size_t n, void *u)
{
    if (!p) return a_malloc(n, u);
    size_t old = *(size_t *)((unsigned char *)p - 16);
    if (n <= old) return p;
    void *q = a_malloc(n, u);
    if (q) memcpy(q, p, old);
    return q;
}

/* ---- real file access (stock functions; the firmware never sees it) ---- */
static size_t rd_cb(void *u, void *buf, size_t n)
{
    vslot *s = u;
    ((fseek_t)o_seek)(s->fp, (int)s->rpos, 0);
    int got = ((fread_t)o_fread)(buf, 1, (int)n, s->fp);
    if (got < 0) got = 0;
    s->rpos += (u32)got;
    return (size_t)got;
}
static drflac_bool32 sk_cb(void *u, int off, drflac_seek_origin o)
{
    vslot *s = u;
    if (o == DRFLAC_SEEK_SET) s->rpos = (u32)off;
    else if (o == DRFLAC_SEEK_CUR) s->rpos += (u32)off;
    else return DRFLAC_FALSE;
    return DRFLAC_TRUE;
}
static drflac_bool32 tl_cb(void *u, drflac_int64 *pos)
{
    *pos = ((vslot *)u)->rpos;
    return DRFLAC_TRUE;
}

static void put32(u8 *p, u32 v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static void put16(u8 *p, u32 v) { p[0] = v; p[1] = v >> 8; }

/* The stock WAV parser needs more than a bare 44-byte header (a minimal
 * WAV is rejected as "collapsed", emu-proven 2026-10-11; ffmpeg's layout
 * with a LIST chunk before data parses): RIFF, fmt, LIST INFO (ISFT +
 * the FLAC's own tags), data. */
static const char INFO_ID[4][5] = {"IART", "INAM", "IPRD", "IGNR"};

static u32 list_item(u8 *p, const char *id, const char *txt)
{
    u32 n = 0;
    while (txt[n]) n++;
    memcpy(p, id, 4);
    put32(p + 4, n + 1);
    memcpy(p + 8, txt, n + 1);
    u32 len = 8 + n + 1;
    if (len & 1) p[len++] = 0;
    return len;
}

static void make_header(vslot *s)
{
    u32 data = s->frames * s->bpf;
    u8 *h = s->hdr;
    memcpy(h, "RIFF\0\0\0\0WAVEfmt ", 16);
    put32(h + 16, 16);
    put16(h + 20, 1);                            /* PCM */
    put16(h + 22, s->ch);
    put32(h + 24, s->rate);
    put32(h + 28, s->rate * s->bpf);
    put16(h + 32, s->bpf);
    put16(h + 34, s->bps * 8);
    u32 o = 36;
    memcpy(h + o, "LIST\0\0\0\0INFO", 12);
    u32 l0 = o;
    o += 12;
    o += list_item(h + o, "ISFT", s->kind == 2 ? "XDJ-700 mods Opus" : "XDJ-700 mods FLAC");
    for (int i = 0; i < 4; i++)
        if (s->tag[i][0]) o += list_item(h + o, INFO_ID[i], s->tag[i]);
    put32(h + l0 + 4, o - l0 - 8);
    memcpy(h + o, "data", 4);
    put32(h + o + 4, data);
    o += 8;
    s->hdrlen = o;
    put32(h + 4, o - 8 + data);
}

static int keyeq(const char *c, u32 n, const char *key)
{
    u32 k = 0;
    for (; key[k]; k++) {
        if (k >= n) return 0;
        char a = c[k];
        if (a >= 'a' && a <= 'z') a -= 32;
        if (a != key[k]) return 0;
    }
    return k < n && c[k] == '=';
}

static void on_meta(void *u, drflac_metadata *md)
{
    static const char *keys[4] = {"ARTIST", "TITLE", "ALBUM", "GENRE"};
    vslot *s = u;
    if (md->type != DRFLAC_METADATA_BLOCK_TYPE_VORBIS_COMMENT) return;
    drflac_vorbis_comment_iterator it;
    drflac_init_vorbis_comment_iterator(&it, md->data.vorbis_comment.commentCount,
                                        md->data.vorbis_comment.pComments);
    const char *c;
    drflac_uint32 n;
    while ((c = drflac_next_vorbis_comment(&it, &n)) != 0)
        for (int i = 0; i < 4; i++) {
            u32 kl = 0;
            while (keys[i][kl]) kl++;
            if (s->tag[i][0] || !keyeq(c, n, keys[i])) continue;
            u32 m = n - kl - 1;
            if (m > 63) m = 63;                  /* UTF-8 passes through */
            memcpy(s->tag[i], c + kl + 1, m);
            s->tag[i][m] = 0;
        }
}

static vslot *find(void *fp)
{
    for (int i = 0; i < NSLOT; i++) if (S[i].fp == fp) return &S[i];
    return 0;
}

static int is_neg(void *fp)
{
    for (int i = 0; i < NNEG; i++) if (neg[i] == fp) return 1;
    return 0;
}


#ifdef VF_OPUS
/* ================= Ogg Opus ================= */
#define PKT_MAX 8192
#define PREROLL 3840                 /* 80 ms at 48 kHz */

static int rd_at(vslot *s, u32 off, void *buf, u32 n)
{
    s->rpos = off;
    return (int)rd_cb(s, buf, n);
}

static u32 le32(const u8 *p) { return p[0] | p[1] << 8 | p[2] << 16 | (u32)p[3] << 24; }

/* page header at off -> lace table + granule; body is NOT read */
static int pg_head(vslot *s, u32 off, u32 *len, u32 *gran, u8 *flags)
{
    u8 h[27];
    if (off + 27 > s->fsize || rd_at(s, off, h, 27) != 27) return 0;
    if (memcmp(h, "OggS", 4) || h[4] != 0) return 0;
    u32 n = h[26], body = 0;
    if (rd_at(s, off + 27, s->lace, n) != (int)n) return 0;
    for (u32 i = 0; i < n; i++) body += s->lace[i];
    *len = 27 + n + body;
    /* granule: 64-bit LE; -1 = no packet ends here. 48 kHz fits 32 bits
     * for ~24 h, so the high word must be 0 (or all ones = -1). */
    u32 lo = le32(h + 6), hi = le32(h + 10);
    *gran = hi == 0 ? lo : 0xFFFFFFFFu;
    *flags = h[5];
    s->nseg = n;
    return 1;
}

static int pg_load(vslot *s, u32 off)
{
    u32 len, gran;
    u8 fl;
    if (!pg_head(s, off, &len, &gran, &fl)) return 0;
    u32 body = len - 27 - s->nseg;
    if (body > 65536 || rd_at(s, off + 27 + s->nseg, s->pg, body) != (int)body) return 0;
    s->pg_off = off;
    s->pg_next = off + len;
    s->pg_gran = gran;
    s->pg_flags = fl;
    s->seg_i = s->body_i = 0;
    return 1;
}

/* next complete packet -> s->pkt/pkt_len; 0 = end of stream */
static int next_packet(vslot *s)
{
    if (s->pkt_done) {                           /* previous one was handed out */
        s->pkt_len = 0;
        s->pkt_done = 0;
    }
    for (;;) {
        if (s->seg_i >= s->nseg) {
            if (s->pg_next >= s->fsize || !pg_load(s, s->pg_next)) return 0;
            if (!(s->pg_flags & 1)) {            /* not a continuation */
                s->pkt_len = 0;
                s->skip_cont = 0;
            }
        }
        u32 l = s->lace[s->seg_i++];
        if (!s->skip_cont && s->pkt_len + l <= PKT_MAX)
            memcpy(s->pkt + s->pkt_len, s->pg + s->body_i, l);
        s->pkt_len += l;
        s->body_i += l;
        if (l < 255) {                           /* packet ends here */
            if (s->skip_cont) {                  /* tail of a packet we missed */
                s->skip_cont = 0;
                s->pkt_len = 0;
                continue;
            }
            if (s->pkt_len > PKT_MAX) { s->pkt_len = 0; continue; }
            s->pkt_done = 1;
            return 1;
        }
    }
}

/* start decoding at the page at `off`: the first packet that starts on it
 * begins at page granule - durations of the packets completed on it */
static int resync(vslot *s, u32 off)
{
    if (!pg_load(s, off)) return 0;
    u32 total = 0, i = 0, b = 0, cont = s->pg_flags & 1, start_b = 0, plen = 0;
    int first = 1;
    for (; i < s->nseg; i++) {
        u32 l = s->lace[i];
        if (plen == 0) start_b = b;
        plen += l;
        b += l;
        if (l < 255) {
            if (!(first && cont))
                total += (u32)opus_packet_get_nb_samples(s->pg + start_b, (opus_int32)plen, 48000);
            first = 0;
            plen = 0;
        }
    }
    s->smp = s->pg_gran != 0xFFFFFFFFu && s->pg_gran >= total ? s->pg_gran - total : s->gran0;
    s->seg_i = s->body_i = 0;
    s->pkt_len = s->pkt_done = 0;
    s->skip_cont = cont;                         /* drop the partial packet */
    opus_decoder_init(s->od, 48000, s->ch);
    s->pcm_n = 0;
    return 1;
}

/* find the first page header at or after `off` (before `end`) */
static u32 find_page(vslot *s, u32 off, u32 end)
{
    u8 *buf = s->scan;            /* arena, not the caller's task stack */
    while (off + 27 <= end) {
        int n = rd_at(s, off, buf, 4096 + 3);
        if (n < 4) return 0;
        for (int i = 0; i + 4 <= n; i++)
            if (buf[i] == 'O' && !memcmp(buf + i, "OggS", 4)) {
                u32 len, g;
                u8 fl;
                if (pg_head(s, off + i, &len, &g, &fl)) return off + i;
            }
        off += 4096;
    }
    return 0;
}

static int opus_seek(vslot *s, u32 k)
{
    vf_stats[5]++;
    long long t = (long long)k + s->pre_skip - PREROLL;
    u32 best = s->audio_start;
    if (t > 0) {
        u32 lo = s->audio_start, hi = s->fsize;
        for (int it = 0; it < 40 && hi > lo + 4096; it++) {
            u32 mid = lo + (hi - lo) / 2, p = find_page(s, mid, hi), len, g;
            u8 fl;
            if (!p || !pg_head(s, p, &len, &g, &fl) || g == 0xFFFFFFFFu ||
                (long long)g > t) {
                hi = mid;
                continue;
            }
            best = p;
            lo = p + len;
        }
    }
    return resync(s, best);
}

/* decoded packet holding output frame k -> s->pcm */
static int opus_fill(vslot *s, u32 k)
{
    if (k >= s->frames) return 0;
    int behind = s->pcm_n == 0 || (long long)k < s->pcm_start ||
                 (long long)k >= (long long)s->pcm_start + s->pcm_n + 48000 * 2;
    if (behind && !opus_seek(s, k)) return 0;
    for (int guard = 0; guard < 100000; guard++) {
        if (s->pcm_n && (long long)k >= s->pcm_start &&
            (long long)k < (long long)s->pcm_start + s->pcm_n) return 1;
        if (!next_packet(s)) return 0;
        vf_stats[6]++;
        int n = opus_decode(s->od, s->pkt, (opus_int32)s->pkt_len, s->pcm, 5760, 0);
        if (n < 0) {                             /* damaged: keep time, silence */
            n = opus_packet_get_nb_samples(s->pkt, (opus_int32)s->pkt_len, 48000);
            if (n <= 0 || n > 5760) n = 960;
            memset(s->pcm, 0, (u32)n * s->ch * 2);
        }
        s->pcm_start = (int)s->smp - (int)s->pre_skip;
        s->pcm_n = (u32)n;
        s->smp += (u32)n;
    }
    return 0;
}

static void opus_tags(vslot *s, const u8 *p, u32 n)
{
    static const char *keys[4] = {"ARTIST", "TITLE", "ALBUM", "GENRE"};
    if (n < 16 || memcmp(p, "OpusTags", 8)) return;
    u32 o = 8, vl = le32(p + 8);
    o += 4 + vl;
    if (o + 4 > n) return;
    u32 cnt = le32(p + o);
    o += 4;
    for (u32 c = 0; c < cnt && o + 4 <= n; c++) {
        u32 l = le32(p + o);
        o += 4;
        if (l > n - o) break;
        for (int i = 0; i < 4; i++) {
            u32 kl = 0;
            while (keys[i][kl]) kl++;
            if (s->tag[i][0] || !keyeq((const char *)p + o, l, keys[i])) continue;
            u32 m = l - kl - 1;
            if (m > 63) m = 63;
            memcpy(s->tag[i], p + o + kl + 1, m);
            s->tag[i][m] = 0;
        }
        o += l;
    }
}

/* "OggS" handle: OpusHead, OpusTags, length from the last page */
static int opus_open(vslot *s)
{
    u32 sz = 0;
    if (((fsize_t)o_size)(s->fp, &sz) != 0 && sz == 0) return 0;
    s->fsize = sz;
    s->kind = 2;
    int st = opus_decoder_get_size(2);
    s->od = a_malloc((size_t)st, s);
    s->pg = a_malloc(65536, s);
    s->pkt = a_malloc(PKT_MAX, s);
    s->pcm = a_malloc(5760 * 2 * 2, s);
    s->scan = a_malloc(4096 + 4, s);
    if (!s->od || !s->pg || !s->pkt || !s->pcm || !s->scan) return 0;
    s->pg_next = 0;
    s->nseg = s->seg_i = 0;
    if (!next_packet(s) || s->pkt_len < 19 || memcmp(s->pkt, "OpusHead", 8)) return 0;
    u8 ch = s->pkt[9], fam = s->pkt[18];
    if (ch < 1 || ch > 2 || fam != 0) return 0;
    s->ch = ch;
    s->pre_skip = s->pkt[10] | s->pkt[11] << 8;
    if (!next_packet(s)) return 0;               /* OpusTags (may span pages) */
    opus_tags(s, s->pkt, s->pkt_len > PKT_MAX ? PKT_MAX : s->pkt_len);
    s->audio_start = s->pg_next;                 /* tags end their page */
    /* length: last page with a granule in the file's tail */
    u32 last = 0;
    for (u32 back = 65536; back <= 4 * 65536 && !last; back *= 2) {
        u32 from = sz > back ? sz - back : s->audio_start, p = from;
        while ((p = find_page(s, p, sz)) != 0) {
            u32 len, g;
            u8 fl;
            pg_head(s, p, &len, &g, &fl);
            if (g != 0xFFFFFFFFu) last = g;
            p += len;
        }
    }
    if (last <= s->pre_skip) return 0;
    {   /* granule at the start of audio (normally 0) */
        u32 len, g;
        u8 fl;
        s->gran0 = 0;
        if (pg_head(s, s->audio_start, &len, &g, &fl) && g != 0xFFFFFFFFu) {
            if (!resync(s, s->audio_start)) return 0;
            s->gran0 = s->smp;
        }
    }
    s->rate = 48000;
    s->bps = 2;
    s->bpf = s->ch * 2;
    s->frames = last - s->pre_skip;
    if (opus_decoder_init(s->od, 48000, s->ch) != OPUS_OK) return 0;
    s->pcm_n = 0;
    return 1;
}
#endif

/* first touch of a handle: FLAC / Opus? (restores the real position) */
static vslot *probe(void *fp)
{
    vslot *s = find(fp);
    if (s) {
        /* 2026-10-21: a handle can be freed WITHOUT vf_close (a reader
         * that closes through the stock path) and its address reused by
         * an ordinary file; the raw-pointer match would then serve the
         * old FLAC's virtual WAV for every read of the new file. Re-check
         * the magic before trusting a slot (same tell/seek/read/seek-back
         * the first-touch probe below does; restores the position). */
        int pos = ((ftell_t)o_tell)(fp);
        u8 m[4] = {0};
        ((fseek_t)o_seek)(fp, 0, 0);
        int got = ((fread_t)o_fread)(m, 1, 4, fp);
        ((fseek_t)o_seek)(fp, pos, 0);
        if (got != 4 || memcmp(m, s->kind == 1 ? "fLaC" : "OggS", 4)) {
            memset(s, 0, offsetof(vslot, cache));
            neg[negi++ % NNEG] = fp;
            vf_stats[5]++;            /* stale-slot recoveries */
            return 0;
        }
        return s;
    }
    if (is_neg(fp)) return 0;
    vf_stats[0]++;
    int pos = ((ftell_t)o_tell)(fp);
    u8 m[4] = {0};
    ((fseek_t)o_seek)(fp, 0, 0);
    int got = ((fread_t)o_fread)(m, 1, 4, fp);
    ((fseek_t)o_seek)(fp, pos, 0);
    int is_ogg = 0;
#ifdef VF_OPUS
    is_ogg = got == 4 && !memcmp(m, "OggS", 4);
#endif
    if (got != 4 || (memcmp(m, "fLaC", 4) && !is_ogg)) {
        neg[negi++ % NNEG] = fp;
        return 0;
    }
    for (int i = 0; i < NSLOT; i++) if (!S[i].fp) { s = &S[i]; break; }
    if (!s) s = &S[0];                           /* evict the oldest */
    memset(s, 0, offsetof(vslot, cache));
    s->fp = fp;
#ifdef VF_OPUS
    if (is_ogg) {
        int okk = opus_open(s);
        ((fseek_t)o_seek)(fp, pos, 0);
        if (!okk) {
            s->fp = 0;
            neg[negi++ % NNEG] = fp;
            return 0;
        }
        vf_stats[4]++;
        s->cache_n = 0;
        make_header(s);
        s->vsize = s->hdrlen + s->frames * s->bpf;
        return s;
    }
#endif
    s->kind = 1;
    drflac_allocation_callbacks cb = { s, a_malloc, a_realloc, a_free };
    s->f = drflac_open_with_metadata(rd_cb, sk_cb, tl_cb, on_meta, s, &cb);
    if (!s->f || !(s->f->sampleRate == 44100 || s->f->sampleRate == 48000) ||
        s->f->channels < 1 || s->f->channels > 2) {
        s->fp = 0;                               /* not playable: stock path */
        neg[negi++ % NNEG] = fp;
        return 0;
    }
    vf_stats[1]++;
    s->rate = s->f->sampleRate;
    s->ch = (u8)s->f->channels;
    s->bps = s->f->bitsPerSample > 16 ? 3 : 2;
    s->bpf = s->ch * s->bps;
    s->frames = (u32)s->f->totalPCMFrameCount;
    s->cache_n = 0;
    make_header(s);
    s->vsize = s->hdrlen + s->frames * s->bpf;
    return s;
}

/* decoded frame k (s32, left-justified by dr_flac) -> cache */
static int fill(vslot *s, u32 k)
{
    if (k >= s->frames) return 0;
    if (k != s->next_frame) {
        if (!drflac_seek_to_pcm_frame(s->f, k)) return 0;
        s->next_frame = k;
    }
    u32 n = (u32)drflac_read_pcm_frames_s32(s->f, CACHE_FRAMES, s->cache);
    vf_stats[3]++;
    s->cache_frame = k;
    s->cache_n = n;
    s->next_frame = k + n;
    return n != 0;
}

static int vread(vslot *s, u8 *out, u32 n)
{
    u32 done = 0;
    vf_stats[2]++;
    while (done < n && s->vpos < s->vsize) {
        if (s->vpos < s->hdrlen) {
            out[done++] = s->hdr[s->vpos++];
            continue;
        }
        u32 rel = s->vpos - s->hdrlen, fr = rel / s->bpf, b = rel % s->bpf;
#ifdef VF_OPUS
        if (s->kind == 2) {                      /* s16 LE straight from pcm */
            if (!opus_fill(s, fr)) break;
            u32 off = (u32)((int)fr - s->pcm_start) * s->bpf + b;
            u32 avail = s->pcm_n * s->bpf - off;
            if (avail > n - done) avail = n - done;
            if (avail > s->vsize - s->vpos) avail = s->vsize - s->vpos;
            const u8 *src = (const u8 *)s->pcm + off;
            for (u32 i = 0; i < avail; i++) out[done + i] = src[i];
            done += avail;
            s->vpos += avail;
            continue;
        }
#endif
        if (!(s->cache_n && fr >= s->cache_frame && fr < s->cache_frame + s->cache_n))
            if (!fill(s, fr)) break;
        const int *smp = s->cache + (fr - s->cache_frame) * s->ch;
        u32 avail = (s->cache_frame + s->cache_n - fr) * s->bpf - b;
        if (avail > n - done) avail = n - done;
        if (avail > s->vsize - s->vpos) avail = s->vsize - s->vpos;
        for (u32 i = 0; i < avail; i++) {
            u32 p = b + i, c = (p / s->bps) % s->ch, byte = p % s->bps;
            int v = smp[(p / s->bpf) * s->ch + c];
            /* s32 left-justified: 16-bit -> bytes 2,3 ; 24-bit -> 1,2,3 */
            out[done + i] = (u8)(v >> (8 * (4 - s->bps + byte)));
        }
        done += avail;
        s->vpos += avail;
    }
    return (int)done;
}

/* ================= hooks (same signatures as the stock slots) ========== */
int vf_fread(void *buf, int size, int count, void *fp)
{
    vslot *s = probe(fp);
    if (!s) return ((fread_t)vf_read_prev)(buf, size, count, fp);
    if (size <= 0 || count <= 0) return 0;
    u32 p0 = s->vpos;
    int r = vread(s, buf, (u32)(size * count)) / size;
    L(1, fp, p0, (u32)(size * count), (u32)r);
    return r;
}

int vf_fread_direct(void *buf, int size, int count, void *fp)
{
    vslot *s = probe(fp);
    if (!s) return ((fread_t)vf_direct_prev)(buf, size, count, fp);
    if (size <= 0 || count <= 0) return 0;
    u32 p0 = s->vpos;
    int r = vread(s, buf, (u32)(size * count)) / size;
    L(7, fp, p0, (u32)(size * count), (u32)r);
    return r;
}

int vf_seek(void *fp, int off, int whence)
{
    vslot *s = probe(fp);
    if (!s) return ((fseek_t)o_seek)(fp, off, whence);
    long long p = whence == 0 ? off : whence == 1 ? (long long)s->vpos + off
                : (long long)s->vsize + off;
    if (p < 0) p = 0;
    if (p > s->vsize) p = s->vsize;
    s->vpos = (u32)p;
    L(2, fp, (u32)off, (u32)whence, s->vpos);
    return 0;
}

int vf_tell(void *fp)
{
    vslot *s = probe(fp);
    if (s) L(3, fp, 0, 0, s->vpos);
    return s ? (int)s->vpos : ((ftell_t)o_tell)(fp);
}

int vf_eof(void *fp)
{
    vslot *s = probe(fp);
    if (s) L(4, fp, s->vpos, s->vsize, s->vpos >= s->vsize);
    return s ? s->vpos >= s->vsize : ((ftell_t)o_eof)(fp);
}

int vf_size(void *fp, u32 *out)
{
    vslot *s = probe(fp);
    if (!s) return ((fsize_t)o_size)(fp, out);
    if (out) *out = s->vsize;
    L(5, fp, 0, 0, s->vsize);
    return 0;
}

int vf_close(void *fp)
{
    vslot *s = find(fp);
    if (s) { s->fp = 0; L(6, fp, 0, 0, 0); }
    for (int i = 0; i < NNEG; i++) if (neg[i] == fp) neg[i] = 0;
    return ((fclose_t)o_close)(fp);
}

#ifndef VF_NATIVE
__attribute__((section(".entry"), used))
const void *const vf_entry[8] = {
    (const void *)vf_fread, (const void *)vf_fread_direct, (const void *)vf_seek,
    (const void *)vf_tell, (const void *)vf_eof, (const void *)vf_size,
    (const void *)vf_close, 0
};
#endif
