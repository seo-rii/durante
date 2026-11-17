/* Fixed exploratory corpus: each kernel stresses one cost a work count may miss. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint64_t lcg(uint64_t *s) { *s = *s * 6364136223846793005ULL + 1442695040888963407ULL; return *s >> 17; }
static volatile uint64_t sink;

static uint64_t int_chain(long n) { uint64_t x = 1; for (long i = 0; i < n; i++) x = x * 2862933555777941757ULL + 3037000493ULL; return x; }
static uint64_t int_ilp(long n) { uint64_t a = 1, b = 2, c = 3, d = 4; for (long i = 0; i < n; i++) { a += i ^ b; b += i ^ c; c += i ^ d; d += i ^ a; } return a + b + c + d; }
static uint64_t branch_pred(long n) { uint64_t s = 0; for (long i = 0; i < n; i++) { if ((i & 7) < 6) s += i; else s ^= i; } return s; }
static uint64_t branch_rand(long n) { uint64_t s = 0, r = 7; for (long i = 0; i < n; i++) { if (lcg(&r) & 1) s += i; else s ^= i; } return s; }
static uint64_t branch_rand_cmov(long n) { uint64_t s = 0, r = 7; for (long i = 0; i < n; i++) { uint64_t b = lcg(&r) & 1; uint64_t t = s + i, u = s ^ i; s = b ? t : u; } return s; }
static void quick(int *a, long lo, long hi) {
  while (lo < hi) { int p = a[(lo + hi) / 2]; long i = lo, j = hi;
    while (i <= j) { while (a[i] < p) i++; while (a[j] > p) j--; if (i <= j) { int t = a[i]; a[i] = a[j]; a[j] = t; i++; j--; } }
    if (j - lo < hi - i) { quick(a, lo, j); lo = i; } else { quick(a, i, hi); hi = j; } }
}
static uint64_t sort_rand(long n) { int *a = malloc(n * sizeof(int)); uint64_t r = 3; for (long i = 0; i < n; i++) a[i] = (int)lcg(&r);
  quick(a, 0, n - 1); uint64_t s = 0; for (long i = 0; i < n; i += 97) s += (uint64_t)a[i] * i; free(a); return s; }
static uint64_t sort_sorted(long n) { int *a = malloc(n * sizeof(int)); for (long i = 0; i < n; i++) a[i] = (int)i;
  quick(a, 0, n - 1); uint64_t s = 0; for (long i = 0; i < n; i += 97) s += (uint64_t)a[i] * i; free(a); return s; }
static uint64_t chase(long n, long words) { long *next = malloc(words * sizeof(long)); for (long i = 0; i < words; i++) next[i] = i;
  uint64_t r = 11; for (long i = words - 1; i > 0; i--) { long j = lcg(&r) % i; long t = next[i]; next[i] = next[j]; next[j] = t; }
  long p = 0; for (long i = 0; i < n; i++) p = next[p]; free(next); return (uint64_t)p; }
static uint64_t seq_read(long n, long words) { uint64_t *v = malloc(words * 8); for (long i = 0; i < words; i++) v[i] = i;
  uint64_t s = 0; for (long k = 0; k < n; k++) for (long i = 0; i < words; i++) s += v[i]; free(v); return s; }
static uint64_t fp_chain(long n) { double x = 1.0; for (long i = 0; i < n; i++) x = x * 1.0000001 + 0.5e-9; return (uint64_t)(x * 1e6); }
static uint64_t fp_ilp(long n) { double a = 1, b = 2, c = 3, d = 4; for (long i = 0; i < n; i++) { a += 1.5; b *= 1.0000001; c += b; d -= 0.25; } return (uint64_t)(a + b + c + d); }
static uint64_t div_chain(long n) { uint64_t x = ~0ULL; for (long i = 1; i <= n; i++) x = x / ((uint64_t)(i & 15) + 2) + 0x9e3779b97f4a7c15ULL; return x; }
static uint64_t fib(int k) { return k < 2 ? (uint64_t)k : fib(k - 1) + fib(k - 2); }
static uint64_t hash_insert(long n, long slots) { uint64_t *t = calloc(slots, 8); uint64_t r = 5, mask = slots - 1;
  for (long i = 0; i < n; i++) { uint64_t k = lcg(&r) | 1, h = (k * 0x9e3779b97f4a7c15ULL) & mask; while (t[h] && t[h] != k) h = (h + 1) & mask; t[h] = k; }
  uint64_t s = 0; for (long i = 0; i < slots; i += 101) s += t[i]; free(t); return s; }
static uint64_t matmul(long n) { double *a = malloc(n * n * 8), *b = malloc(n * n * 8), *c = calloc(n * n, 8);
  for (long i = 0; i < n * n; i++) { a[i] = (double)(i % 7); b[i] = (double)(i % 5); }
  for (long i = 0; i < n; i++) for (long k = 0; k < n; k++) { double x = a[i * n + k]; for (long j = 0; j < n; j++) c[i * n + j] += x * b[k * n + j]; }
  double s = 0; for (long i = 0; i < n * n; i += 13) s += c[i]; free(a); free(b); free(c); return (uint64_t)s; }
static uint64_t bytes(long n) { unsigned char *buf = malloc(1 << 16); uint64_t s = 0; for (long k = 0; k < n; k++) { for (int i = 0; i < (1 << 16); i++) buf[i] = (unsigned char)(i * k + 3); for (int i = 0; i < (1 << 16); i += 3) s += buf[i]; } free(buf); return s; }

int main(int argc, char **argv) {
  if (argc != 3) return 2;
  const char *w = argv[1]; long s = atol(argv[2]); uint64_t r;
  if (!strcmp(w, "int_chain")) r = int_chain(40000000 * s);
  else if (!strcmp(w, "int_ilp")) r = int_ilp(25000000 * s);
  else if (!strcmp(w, "branch_pred")) r = branch_pred(40000000 * s);
  else if (!strcmp(w, "branch_rand")) r = branch_rand(20000000 * s);
  else if (!strcmp(w, "branch_rand_cmov")) r = branch_rand_cmov(20000000 * s);
  else if (!strcmp(w, "sort_rand")) r = sort_rand(1500000 * s);
  else if (!strcmp(w, "sort_sorted")) r = sort_sorted(3000000 * s);
  else if (!strcmp(w, "chase_l1")) r = chase(20000000 * s, 2048);
  else if (!strcmp(w, "chase_l2")) r = chase(15000000 * s, 1 << 15);
  else if (!strcmp(w, "chase_dram")) r = chase(3000000 * s, 1 << 23);
  else if (!strcmp(w, "seq_read")) r = seq_read(6 * s, 1 << 23);
  else if (!strcmp(w, "fp_chain")) r = fp_chain(20000000 * s);
  else if (!strcmp(w, "fp_ilp")) r = fp_ilp(30000000 * s);
  else if (!strcmp(w, "div_chain")) r = div_chain(6000000 * s);
  else if (!strcmp(w, "fib")) r = fib(28 + (int)s);
  else if (!strcmp(w, "hash_insert")) r = hash_insert(1500000 * s, 1 << 22);
  else if (!strcmp(w, "matmul")) r = matmul(260 * s);
  else if (!strcmp(w, "bytes")) r = bytes(600 * s);
  else return 2;
  sink = r;
  printf("%s %llu\n", w, (unsigned long long)r);
  return 0;
}
