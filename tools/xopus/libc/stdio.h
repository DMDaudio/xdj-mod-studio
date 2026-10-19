#pragma once
/* debug printing is compiled out (no ENABLE_ASSERTIONS); keep the names */
typedef struct _xo_file FILE;
#define stderr ((FILE *)0)
static inline int fprintf(FILE *f, const char *s, ...) { (void)f; (void)s; return 0; }
static inline int printf(const char *s, ...) { (void)s; return 0; }
