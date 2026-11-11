#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

/* Only this fixture and at most one finite child run. No address-space escape,
 * namespace mutation, external PID signalling or privileged operation occurs. */
static void work(void) {
  for (volatile unsigned int i = 0; i < 10000; ++i) {}
}

static int finish_fork(long child) {
  if (child < 0) return 2;
  if (child == 0) { work(); _exit(0); }
  int status;
  if (waitpid((pid_t)child, &status, 0) != child ||
      !WIFEXITED(status) || WEXITSTATUS(status) != 0) return 3;
  puts("ok");
  return 0;
}

int main(int argc, char **argv) {
  if (argc != 2) return 4;
  if (!strcmp(argv[1], "native-fork")) return finish_fork(fork());
  if (!strcmp(argv[1], "native-getpid")) {
    if (syscall(SYS_getpid) != getpid()) return 5;
  } else if (!strcmp(argv[1], "native-enosys") ||
             !strcmp(argv[1], "native-large-enosys")) {
    long number = !strcmp(argv[1], "native-enosys") ? -1 : 0x20000000;
    errno = 0;
    if (syscall(number) != -1 || errno != ENOSYS) return 9;
  } else if (!strcmp(argv[1], "unreachable-int80")) {
    __asm__ volatile("jmp 1f\n\tmov $2, %%eax\n\tint $0x80\n1:"
                     : : : "rax", "memory");
  } else if (!strcmp(argv[1], "int80-fork") ||
             !strcmp(argv[1], "int80-getpid")) {
    int number = !strcmp(argv[1], "int80-fork") ? 2 : 20;
    int result;
    __asm__ volatile("int $0x80" : "=a"(result) : "a"(number)
                     : "memory", "r8", "r9", "r10", "r11");
    if (number == 2) return finish_fork(result);
    if (result != getpid()) return 6;
  } else if (!strcmp(argv[1], "x32-fork") ||
             !strcmp(argv[1], "x32-getpid")) {
    int number = !strcmp(argv[1], "x32-fork") ? SYS_fork : SYS_getpid;
    long result = syscall(0x40000000UL | number);
    if (result == -1 && errno == ENOSYS) { puts("unavailable"); return 0; }
    if (number == SYS_fork) return finish_fork(result);
    if (result != getpid()) return 7;
  } else return 8;
  puts("ok");
  return 0;
}
