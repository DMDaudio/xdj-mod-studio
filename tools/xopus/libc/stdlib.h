#pragma once
#include <stddef.h>
void abort(void);
static inline int abs(int x) { return x < 0 ? -x : x; }
/* heap is never used by the decoder path (gc-sections drops create()) */
void *malloc(size_t);
void free(void *);
void *calloc(size_t, size_t);
void *realloc(void *, size_t);
