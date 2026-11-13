#define _GNU_SOURCE
#include <cpuid.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <ucontext.h>
#include <unistd.h>

/* Compile the C handlers with -mgeneral-regs-only. Only the assembly below may
 * touch vector/x87 state, including around the nested signal's syscall. */
int selected_level, self_pid, self_tid, asynchronous;
volatile sig_atomic_t outer_done, outer_count, nested_count, frame_checks, stack_checks;
unsigned char pattern_app[64] __attribute__((aligned(64)));
unsigned char pattern_nested[64] __attribute__((aligned(64)));
unsigned char result_app[64] __attribute__((aligned(64)));
unsigned char result_nested[64] __attribute__((aligned(64)));
uint16_t cw_app, sw_app, cw_nested, sw_nested;
uint32_t mx_app, mx_nested;
uint64_t value_app, value_nested, mask_app, mask_nested;
int rounded_x87_app, rounded_sse_app, rounded_x87_nested, rounded_sse_nested;
const uint16_t round_down = 0x077f, round_up = 0x0b7f;
const uint32_t mx_down = 0x3f80, mx_up = 0x5f80, mx_default = 0x1f80;
const uint64_t one_half = UINT64_C(0x3ff8000000000000);
const uint64_t alarm_mask = UINT64_C(1) << (SIGALRM - 1);
static uintptr_t stack_low, stack_high;
static unsigned xsave_offset[8], xsave_size[8];
extern void state_probe(void), nested_probe(void), scramble_state(void);
extern char state_fault[], state_resume[];

_Static_assert(SIGALRM == 14 && SIGUSR2 == 12 && SYS_tgkill == 234 &&
               SYS_rt_sigprocmask == 14, "Linux x86-64 syscall ABI required");

__asm__(
    ".text\n"
    ".macro load_vector pattern\n"
    "cmpl $2, selected_level(%rip); je 2f\n"
    "cmpl $1, selected_level(%rip); je 1f\n"
    "movdqu \\pattern(%rip), %xmm15; jmp 3f\n"
    "1: vmovdqu \\pattern(%rip), %ymm15; jmp 3f\n"
    "2: vmovdqu64 \\pattern(%rip), %zmm31; mov $0xa55a, %eax; kmovw %eax, %k7\n"
    "3:\n.endm\n"
    ".macro store_vector target mask\n"
    "cmpl $2, selected_level(%rip); je 2f\n"
    "cmpl $1, selected_level(%rip); je 1f\n"
    "movdqu %xmm15, \\target(%rip); jmp 3f\n"
    "1: vmovdqu %ymm15, \\target(%rip); jmp 3f\n"
    "2: vmovdqu64 %zmm31, \\target(%rip); kmovw %k7, %eax; mov %rax, \\mask(%rip)\n"
    "3:\n.endm\n"
    ".macro set_state cw mx pattern rx rs\n"
    "fninit; fldcw \\cw(%rip); ldmxcsr \\mx(%rip)\n"
    "fldl one_half(%rip); fistl \\rx(%rip)\n"
    "cvtsd2si one_half(%rip), %eax; mov %eax, \\rs(%rip)\n"
    "load_vector \\pattern\n.endm\n"
    ".macro store_state vector mask cw sw mx value\n"
    "store_vector \\vector \\mask\n"
    "fnstcw \\cw(%rip); fnstsw \\sw(%rip); stmxcsr \\mx(%rip)\n"
    "fstpl \\value(%rip); fninit; ldmxcsr mx_default(%rip)\n.endm\n"
    ".global state_probe, state_fault, state_resume\n"
    "state_probe:\n"
    "set_state round_down mx_down pattern_app rounded_x87_app rounded_sse_app\n"
    "cmpl $0, asynchronous(%rip); jne 4f\n"
    "xor %eax, %eax\n"
    "state_fault: mov (%rax), %eax\n"
    /* The handler skips this clobber, so the old vector remains observable. */
    "pxor %xmm15, %xmm15; jmp state_resume\n"
    "4: mov $14, %eax; mov $1, %edi; lea alarm_mask(%rip), %rsi\n"
    "xor %edx, %edx; mov $8, %r10d; syscall\n"
    "mov $10000000, %ecx\n"
    "5: pause; cmpl $0, outer_done(%rip); jne state_resume; dec %ecx; jnz 5b\n"
    "state_resume:\n"
    "store_state result_app mask_app cw_app sw_app mx_app value_app\n"
    "ret\n"
    ".global nested_probe\n"
    "nested_probe:\n"
    "set_state round_up mx_up pattern_nested rounded_x87_nested rounded_sse_nested\n"
    "mov $234, %eax; mov self_pid(%rip), %edi; mov self_tid(%rip), %esi\n"
    "mov $12, %edx; syscall\n"
    "store_state result_nested mask_nested cw_nested sw_nested mx_nested value_nested\n"
    "ret\n"
    ".global scramble_state\n"
    "scramble_state: fninit; fldz; ldmxcsr mx_default(%rip)\n"
    "pxor %xmm15, %xmm15; cmpl $0, selected_level(%rip); je 6f\n"
    "cmpl $2, selected_level(%rip); je 7f\n"
    "vpxor %ymm15, %ymm15, %ymm15; jmp 6f\n"
    "7: vpxord %zmm31, %zmm31, %zmm31; kxorw %k7, %k7, %k7\n"
    "6: ret\n"
    ".section .note.GNU-stack,\"\",@progbits\n.text\n");

static void require(int condition, int code) {
    if (!condition) _exit(code);
}

static uint64_t little(const unsigned char *p, unsigned bytes) {
    uint64_t value = 0;
    for (unsigned i = 0; i < bytes; ++i) value |= (uint64_t)p[i] << (8 * i);
    return value;
}

static void equal_bytes(const unsigned char *a, const unsigned char *b,
                        unsigned bytes, int code) {
    for (unsigned i = 0; i < bytes; ++i) require(a[i] == b[i], code);
}

/* Linux x86-64 sigcontext ABI: FXSAVE's software-reserved bytes describe the
 * standard (not compacted) XSAVE extension. Component offsets come from CPUID
 * leaf 0xD, never from a fixed CPU model. See asm/sigcontext.h. */
static const unsigned char *component(const unsigned char *fp, unsigned feature) {
    require(little(fp + 464, 4) == UINT32_C(0x46505853), 80);
    uint64_t extended = little(fp + 468, 4), size = little(fp + 480, 4);
    require(extended >= 580 && extended <= 65536 && size >= 576 && size <= extended - 4, 81);
    require(little(fp + extended - 4, 4) == UINT32_C(0x46505845), 82);
    require((little(fp + 472, 8) & (UINT64_C(1) << feature)) != 0 &&
            (little(fp + 512, 8) & (UINT64_C(1) << feature)) != 0 &&
            little(fp + 520, 8) == 0, 83);
    require(xsave_size[feature] != 0 && xsave_offset[feature] >= 576 &&
            xsave_offset[feature] <= size && xsave_size[feature] <= size - xsave_offset[feature], 84);
    return fp + xsave_offset[feature];
}

static void check_frame(ucontext_t *context, int nested) {
    const unsigned char *fp = (const unsigned char *)context->uc_mcontext.fpregs;
    const unsigned char *pattern = nested ? pattern_nested : pattern_app;
    require(fp != NULL, 70);
    require(little(fp, 2) == (nested ? round_up : round_down), 71);
    require((little(fp + 2, 2) & 0x383f) == 0x3820 && little(fp + 4, 1) == 0x80, 72);
    require(little(fp + 24, 4) == ((nested ? mx_up : mx_down) | 0x20), 73);
    require(little(fp + 32, 8) == UINT64_C(0xc000000000000000) &&
            little(fp + 40, 2) == 0x3fff, 74); /* ST(0) == 1.5 */
    if (selected_level < 2) {
        equal_bytes(fp + 160 + 15 * 16, pattern, 16, 75);
        if (selected_level == 1) {
            require(xsave_size[2] >= 16 * 16, 84);
            equal_bytes(component(fp, 2) + 15 * 16, pattern + 16, 16, 76);
        }
    } else {
        require(xsave_size[7] >= 16 * 64 && xsave_size[5] >= 8 * 8, 84);
        equal_bytes(component(fp, 7) + 15 * 64, pattern, 64, 77);
        require(little(component(fp, 5) + 7 * 8, 8) == 0xa55a, 78);
    }
    ++frame_checks;
}

static void check_return(int nested) {
    equal_bytes(nested ? result_nested : result_app, nested ? pattern_nested : pattern_app,
                selected_level == 2 ? 64 : (selected_level == 1 ? 32 : 16), 85);
    require((nested ? cw_nested : cw_app) == (nested ? round_up : round_down), 86);
    require(((nested ? sw_nested : sw_app) & 0x383f) == 0x3820, 87);
    require((nested ? mx_nested : mx_app) == ((nested ? mx_up : mx_down) | 0x20), 88);
    require((nested ? value_nested : value_app) == one_half, 89);
    if (selected_level == 2) require((nested ? mask_nested : mask_app) == 0xa55a, 90);
    require((nested ? rounded_x87_nested : rounded_x87_app) == (nested ? 2 : 1) &&
            (nested ? rounded_sse_nested : rounded_sse_app) == (nested ? 2 : 1), 91);
}

static void handler(int sig, siginfo_t *info, void *raw) {
    unsigned char marker;
    require((uintptr_t)&marker >= stack_low && (uintptr_t)&marker < stack_high, 60);
    ++stack_checks;
    ucontext_t *context = raw;
    if (sig == SIGUSR2) {
        require(outer_count == 1 && nested_count == 0, 61);
        check_frame(context, 1);
        ++nested_count;
        scramble_state();
        return;
    }
    require(sig == (asynchronous ? SIGALRM : SIGSEGV) && outer_count == 0, 62);
    if (!asynchronous) {
        require(info->si_addr == NULL && context->uc_mcontext.gregs[REG_RIP] == (greg_t)state_fault, 63);
        context->uc_mcontext.gregs[REG_RIP] = (greg_t)state_resume;
    }
    check_frame(context, 0);
    ++outer_count;
    nested_probe();
    check_return(1);
    scramble_state();
    outer_done = 1;
}

int main(int argc, char **argv) {
    if (argc != 3 || (strcmp(argv[1], "fault") && strcmp(argv[1], "async"))) return 2;
    asynchronous = !strcmp(argv[1], "async");
    if (!strcmp(argv[2], "sse2")) selected_level = 0;
    else if (!strcmp(argv[2], "avx2")) selected_level = 1;
    else if (!strcmp(argv[2], "avx512")) selected_level = 2;
    else return 2;
    unsigned a, b, c, d;
    uint64_t xcr0 = 0;
    if (__get_cpuid(1, &a, &b, &c, &d) && (c & bit_XSAVE) && (c & bit_OSXSAVE)) {
        unsigned low, high;
        __asm__ volatile("xgetbv" : "=a"(low), "=d"(high) : "c"(0));
        xcr0 = ((uint64_t)high << 32) | low;
    }
    if (selected_level) {
        require(__get_cpuid_count(7, 0, &a, &b, &c, &d), 79);
        if (selected_level == 1 && (!(b & bit_AVX2) || (xcr0 & 6) != 6)) return 79;
        if (selected_level == 2 && (!(b & bit_AVX512F) || (xcr0 & 0xe6) != 0xe6)) return 79;
        for (unsigned feature = 2; feature <= 7; ++feature) {
            __cpuid_count(0xd, feature, a, b, c, d);
            xsave_size[feature] = a; xsave_offset[feature] = b;
        }
    }
    for (unsigned i = 0; i < 64; ++i) {
        pattern_app[i] = (unsigned char)(i + 1);
        pattern_nested[i] = (unsigned char)(0x80 + i);
    }
    self_pid = getpid(); self_tid = (int)syscall(SYS_gettid);
    const size_t stack_size = 128 * 1024;
    void *stack = mmap(NULL, stack_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (stack == MAP_FAILED) return 3;
    stack_low = (uintptr_t)stack; stack_high = stack_low + stack_size;
    stack_t alternate = {.ss_sp = stack, .ss_size = stack_size};
    struct sigaction action = {.sa_sigaction = handler, .sa_flags = SA_SIGINFO | SA_ONSTACK};
    sigemptyset(&action.sa_mask);
    if (sigaltstack(&alternate, NULL) || sigaction(SIGSEGV, &action, NULL) ||
        sigaction(SIGALRM, &action, NULL) || sigaction(SIGUSR2, &action, NULL)) return 4;
    sigset_t mask;
    sigemptyset(&mask);
    if (asynchronous) sigaddset(&mask, SIGALRM);
    if (sigprocmask(SIG_SETMASK, &mask, NULL)) return 5;
    if (asynchronous) {
        struct itimerval timer = {.it_value = {.tv_usec = 5000}};
        if (setitimer(ITIMER_REAL, &timer, NULL)) return 6;
    }
    state_probe();
    require(outer_done == 1 && outer_count == 1 && nested_count == 1 &&
            frame_checks == 2 && stack_checks == 2, 92);
    check_return(0);
    printf("{\"case\":\"%s\",\"isa\":\"%s\",\"outer\":1,\"nested\":1,"
           "\"frames\":2,\"altstack\":2,\"restored\":true}\n", argv[1], argv[2]);
    alternate.ss_flags = SS_DISABLE;
    if (sigaltstack(&alternate, NULL) || munmap(stack, stack_size)) return 7;
    return 0;
}
