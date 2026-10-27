/* a64enc.h -- tiny arm64 instruction encoder for the SH-4 block JIT
 * (emu/shjit.c). Every function returns one 32-bit instruction word.
 * Verified against the system assembler by tools/jit/a64enc_test.py.
 * w = 32-bit forms, x = 64-bit forms. Register 31 = wzr/xzr (or sp where
 * the instruction says so). */
#ifndef A64ENC_H
#define A64ENC_H
#include <stdint.h>

typedef uint32_t A64;
enum { A_EQ = 0, A_NE = 1, A_HS = 2, A_LO = 3, A_MI = 4, A_PL = 5, A_HI = 8,
       A_LS = 9, A_GE = 10, A_LT = 11, A_GT = 12, A_LE = 13 };

/* ---- data processing, register */
static inline A64 a_addw(int d, int n, int m)  { return 0x0B000000u | m << 16 | n << 5 | d; }
static inline A64 a_subw(int d, int n, int m)  { return 0x4B000000u | m << 16 | n << 5 | d; }
static inline A64 a_andw(int d, int n, int m)  { return 0x0A000000u | m << 16 | n << 5 | d; }
static inline A64 a_orrw(int d, int n, int m)  { return 0x2A000000u | m << 16 | n << 5 | d; }
static inline A64 a_eorw(int d, int n, int m)  { return 0x4A000000u | m << 16 | n << 5 | d; }
static inline A64 a_movw(int d, int m)         { return a_orrw(d, 31, m); }
static inline A64 a_mvnw(int d, int m)         { return 0x2A2003E0u | m << 16 | d; }
static inline A64 a_negw(int d, int m)         { return a_subw(d, 31, m); }
static inline A64 a_cmpw(int n, int m)         { return 0x6B00001Fu | m << 16 | n << 5; }
static inline A64 a_tstw(int n, int m)         { return 0x6A00001Fu | m << 16 | n << 5; }
static inline A64 a_mulw(int d, int n, int m)  { return 0x1B007C00u | m << 16 | n << 5 | d; }
static inline A64 a_lslvw(int d, int n, int m) { return 0x1AC02000u | m << 16 | n << 5 | d; }
static inline A64 a_lsrvw(int d, int n, int m) { return 0x1AC02400u | m << 16 | n << 5 | d; }
static inline A64 a_asrvw(int d, int n, int m) { return 0x1AC02800u | m << 16 | n << 5 | d; }
static inline A64 a_cmpx(int n, int m)         { return 0xEB00001Fu | m << 16 | n << 5; }
static inline A64 a_movx(int d, int m)         { return 0xAA0003E0u | m << 16 | d; }
/* add xd, xn, xm, lsl #sh */
static inline A64 a_addx_lsl(int d, int n, int m, int sh)
{ return 0x8B000000u | m << 16 | (uint32_t)sh << 10 | n << 5 | d; }
/* cset wd, cond */
static inline A64 a_csetw(int d, int cc)       { return 0x1A9F07E0u | (uint32_t)(cc ^ 1) << 12 | d; }
/* csel wd, wn, wm, cond */
static inline A64 a_cselw(int d, int n, int m, int cc)
{ return 0x1A800000u | m << 16 | (uint32_t)cc << 12 | n << 5 | d; }

/* ---- data processing, immediate (imm12, no shift) */
static inline A64 a_addiw(int d, int n, uint32_t i) { return 0x11000000u | (i & 0xFFF) << 10 | n << 5 | d; }
static inline A64 a_subiw(int d, int n, uint32_t i) { return 0x51000000u | (i & 0xFFF) << 10 | n << 5 | d; }
static inline A64 a_cmpiw(int n, uint32_t i)        { return 0x7100001Fu | (i & 0xFFF) << 10 | n << 5; }

/* ---- bitfield: ubfm/sbfm/bfm (32-bit) */
static inline A64 a_ubfmw(int d, int n, int r, int s) { return 0x53000000u | (uint32_t)r << 16 | (uint32_t)s << 10 | n << 5 | d; }
static inline A64 a_sbfmw(int d, int n, int r, int s) { return 0x13000000u | (uint32_t)r << 16 | (uint32_t)s << 10 | n << 5 | d; }
static inline A64 a_bfmw(int d, int n, int r, int s)  { return 0x33000000u | (uint32_t)r << 16 | (uint32_t)s << 10 | n << 5 | d; }
static inline A64 a_lslw(int d, int n, int sh) { return a_ubfmw(d, n, (32 - sh) & 31, 31 - sh); }
static inline A64 a_lsrw(int d, int n, int sh) { return a_ubfmw(d, n, sh, 31); }
static inline A64 a_asrw(int d, int n, int sh) { return a_sbfmw(d, n, sh, 31); }
static inline A64 a_uxtbw(int d, int n) { return a_ubfmw(d, n, 0, 7); }
static inline A64 a_uxthw(int d, int n) { return a_ubfmw(d, n, 0, 15); }
static inline A64 a_sxtbw(int d, int n) { return a_sbfmw(d, n, 0, 7); }
static inline A64 a_sxthw(int d, int n) { return a_sbfmw(d, n, 0, 15); }
/* ubfx wd, wn, #lsb, #width */
static inline A64 a_ubfxw(int d, int n, int lsb, int w) { return a_ubfmw(d, n, lsb, lsb + w - 1); }
/* bfi wd, wn, #0, #1  (wd bit 0 = wn bit 0) */
static inline A64 a_bfi0w(int d, int n) { return a_bfmw(d, n, 0, 0); }

/* ---- logical immediate, only the patterns we need (32-bit):
 * low k ones (k = 1..31): N=0 immr=0 imms=k-1
 * the same rotated right by r: immr=r */
static inline A64 a_andiw_ones(int d, int n, int k, int r)
{ return 0x12000000u | (uint32_t)r << 16 | (uint32_t)(k - 1) << 10 | n << 5 | d; }
static inline A64 a_orriw_ones(int d, int n, int k, int r)
{ return 0x32000000u | (uint32_t)r << 16 | (uint32_t)(k - 1) << 10 | n << 5 | d; }
static inline A64 a_tstiw_ones(int n, int k, int r)
{ return 0x7200001Fu | (uint32_t)r << 16 | (uint32_t)(k - 1) << 10 | n << 5; }

/* ---- moves */
static inline A64 a_movzw(int d, uint32_t i16, int hw) { return 0x52800000u | (uint32_t)hw << 21 | (i16 & 0xFFFF) << 5 | d; }
static inline A64 a_movkw(int d, uint32_t i16, int hw) { return 0x72800000u | (uint32_t)hw << 21 | (i16 & 0xFFFF) << 5 | d; }
static inline A64 a_movzx(int d, uint32_t i16, int hw) { return 0xD2800000u | (uint32_t)hw << 21 | (i16 & 0xFFFF) << 5 | d; }
static inline A64 a_movkx(int d, uint32_t i16, int hw) { return 0xF2800000u | (uint32_t)hw << 21 | (i16 & 0xFFFF) << 5 | d; }

/* ---- loads / stores, unsigned scaled offset (bytes, must be aligned) */
static inline A64 a_ldrw_i(int t, int n, uint32_t off) { return 0xB9400000u | (off / 4) << 10 | n << 5 | t; }
static inline A64 a_strw_i(int t, int n, uint32_t off) { return 0xB9000000u | (off / 4) << 10 | n << 5 | t; }
static inline A64 a_ldrx_i(int t, int n, uint32_t off) { return 0xF9400000u | (off / 8) << 10 | n << 5 | t; }
static inline A64 a_strx_i(int t, int n, uint32_t off) { return 0xF9000000u | (off / 8) << 10 | n << 5 | t; }
static inline A64 a_ldrhw_i(int t, int n, uint32_t off) { return 0x79400000u | (off / 2) << 10 | n << 5 | t; }
/* ldp wt, wt2, [xn, #off] (signed offset, off/4 in 7 bits) */
static inline A64 a_ldpw(int t, int t2, int n, int off)
{ return 0x29400000u | (uint32_t)((off / 4) & 0x7F) << 15 | t2 << 10 | n << 5 | t; }
/* register offset: [xn, xm] */
static inline A64 a_ldrw_r(int t, int n, int m)  { return 0xB8606800u | m << 16 | n << 5 | t; }
static inline A64 a_ldrhw_r(int t, int n, int m) { return 0x78606800u | m << 16 | n << 5 | t; }
static inline A64 a_ldrbw_r(int t, int n, int m) { return 0x38606800u | m << 16 | n << 5 | t; }
static inline A64 a_strw_r(int t, int n, int m)  { return 0xB8206800u | m << 16 | n << 5 | t; }
static inline A64 a_strhw_r(int t, int n, int m) { return 0x78206800u | m << 16 | n << 5 | t; }
static inline A64 a_strbw_r(int t, int n, int m) { return 0x38206800u | m << 16 | n << 5 | t; }

/* ---- stack frame */
/* stp xt, xt2, [sp, #off]!  (pre-index) / ldp xt, xt2, [sp], #off (post) */
static inline A64 a_stpx_pre(int t, int t2, int off) { return 0xA9800000u | (uint32_t)((off / 8) & 0x7F) << 15 | t2 << 10 | 31u << 5 | t; }
static inline A64 a_ldpx_post(int t, int t2, int off) { return 0xA8C00000u | (uint32_t)((off / 8) & 0x7F) << 15 | t2 << 10 | 31u << 5 | t; }
static inline A64 a_stpx(int t, int t2, int n, int off) { return 0xA9000000u | (uint32_t)((off / 8) & 0x7F) << 15 | t2 << 10 | n << 5 | t; }
static inline A64 a_ldpx(int t, int t2, int n, int off) { return 0xA9400000u | (uint32_t)((off / 8) & 0x7F) << 15 | t2 << 10 | n << 5 | t; }
static inline A64 a_mov_fp_sp(void) { return 0x910003FDu; }      /* mov x29, sp */

/* ---- branches (offsets in instructions, patched later) */
static inline A64 a_b(int32_t off)            { return 0x14000000u | ((uint32_t)off & 0x3FFFFFFu); }
static inline A64 a_bcc(int cc, int32_t off)  { return 0x54000000u | ((uint32_t)off & 0x7FFFFu) << 5 | (uint32_t)cc; }
static inline A64 a_cbzw(int t, int32_t off)  { return 0x34000000u | ((uint32_t)off & 0x7FFFFu) << 5 | t; }
static inline A64 a_cbnzw(int t, int32_t off) { return 0x35000000u | ((uint32_t)off & 0x7FFFFu) << 5 | t; }
static inline A64 a_blr(int n) { return 0xD63F0000u | n << 5; }
static inline A64 a_br(int n)  { return 0xD61F0000u | n << 5; }
static inline A64 a_cbzx(int t, int32_t off) { return 0xB4000000u | ((uint32_t)off & 0x7FFFFu) << 5 | t; }
/* 64-bit: add xd, xn, #imm12 / cmp xn, #imm12 / madd xd, xn, xm, xa / ldrb wt, [xn, #off] */
static inline A64 a_addix(int d, int n, uint32_t i) { return 0x91000000u | (i & 0xFFF) << 10 | n << 5 | d; }
static inline A64 a_cmpix(int n, uint32_t i) { return 0xF100001Fu | (i & 0xFFF) << 10 | n << 5; }
static inline A64 a_maddx(int d, int n, int m, int a) { return 0x9B000000u | m << 16 | a << 10 | n << 5 | d; }
static inline A64 a_ldrhw_i2(int t, int n, uint32_t off) { return 0x79400000u | (off / 2) << 10 | n << 5 | t; }
static inline A64 a_ldrbw_i(int t, int n, uint32_t off) { return 0x39400000u | (off & 0xFFF) << 10 | n << 5 | t; }
static inline A64 a_ret(void)  { return 0xD65F03C0u; }
#endif
