#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/signalfd.h>
#include <sys/syscall.h>
#include <unistd.h>

#define CHECK(test) do { if (!(test)) { fprintf(stderr, "failed line %d errno %d\n", __LINE__, errno); return 1; } } while (0)
static int shared_fd;
static void *unshare_thread(void *unused) {
  (void)unused;
  return (void *)(long)syscall(SYS_close_range, shared_fd, shared_fd, 2u);
}
int main(int argc, char **argv) {
  CHECK(argc == 2);
  int fd = open("/dev/null", O_RDONLY);
  CHECK(fd >= 3);
  if (!strcmp(argv[1], "invalid")) {
    errno = 0;
    CHECK(syscall(SYS_close_range, fd, UINT_MAX, 8u) == -1 && errno == EINVAL);
    CHECK(fcntl(fd, F_GETFD) >= 0);
    errno = 0;
    CHECK(syscall(SYS_close_range, 9u, 8u, 0u) == -1 && errno == EINVAL);
    CHECK(fcntl(fd, F_GETFD) >= 0);
  } else if (!strcmp(argv[1], "cloexec")) {
    CHECK(syscall(SYS_close_range, fd, UINT_MAX, 4u) == 0);
    int flags = fcntl(fd, F_GETFD);
    CHECK(flags >= 0 && (flags & FD_CLOEXEC) != 0);
  } else if (!strcmp(argv[1], "unshare-thread")) {
    shared_fd = fd;
    pthread_t thread;
    void *result;
    CHECK(pthread_create(&thread, NULL, unshare_thread, NULL) == 0);
    CHECK(pthread_join(thread, &result) == 0 && result == NULL);
    CHECK(fcntl(fd, F_GETFD) >= 0);
  } else if (!strcmp(argv[1], "signalfd")) {
    sigset_t mask;
    CHECK(sigemptyset(&mask) == 0 && sigaddset(&mask, SIGUSR1) == 0);
    CHECK(sigprocmask(SIG_BLOCK, &mask, NULL) == 0);
    int signal_fd = signalfd(-1, &mask, SFD_CLOEXEC);
    CHECK(signal_fd >= 3);
    CHECK(syscall(SYS_close_range, signal_fd, UINT_MAX, 0u) == 0);
    int next = signalfd(-1, &mask, SFD_CLOEXEC);
    CHECK(next >= 3);
    CHECK(kill(getpid(), SIGUSR1) == 0);
    struct signalfd_siginfo info;
    CHECK(read(next, &info, sizeof(info)) == sizeof(info));
    CHECK(info.ssi_signo == SIGUSR1);
    CHECK(close(next) == 0);
  } else if (!strcmp(argv[1], "all")) {
    CHECK(syscall(SYS_close_range, 3u, UINT_MAX, 0u) == 0);
    errno = 0;
    CHECK(fcntl(fd, F_GETFD) == -1 && errno == EBADF);
  } else if (!strcmp(argv[1], "empty")) {
    CHECK(syscall(SYS_close_range, UINT_MAX, UINT_MAX, 0u) == 0);
    CHECK(fcntl(fd, F_GETFD) >= 0);
  } else {
    return 2;
  }
  puts("ok");
  return 0;
}
