#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

extern char **environ;
#define CHECK(test) do { if (!(test)) { fprintf(stderr, "failed line %d errno %d\n", __LINE__, errno); return 2; } } while (0)

/* Deliberately inspect only this fixture's own descriptors. Protected client
 * descriptors prevent accidental close, not /proc visibility or same-UID I/O. */
static int own_report(char *out, size_t capacity) {
  DIR *directory = opendir("/proc/self/fd");
  if (!directory) return -1;
  struct dirent *entry;
  char alias[128], suffix[80];
  snprintf(suffix, sizeof(suffix), "/process-%d.jsonl", getpid());
  while ((entry = readdir(directory))) {
    snprintf(alias, sizeof(alias), "/proc/self/fd/%.64s", entry->d_name);
    ssize_t size = readlink(alias, out, capacity - 1);
    if (size > 0) {
      out[size] = 0;
      if (strstr(out, suffix)) { closedir(directory); return 0; }
    }
  }
  closedir(directory);
  return -1;
}

static int forge_counter(char *buffer, const char *field, char value) {
  char key[80];
  snprintf(key, sizeof(key), "\"%s\":", field);
  char *number = strstr(buffer, key);
  if (!number) return -1;
  number += strlen(key);
  if (*number < '0' || *number > '9') return -1;
  *number++ = value;
  while (*number >= '0' && *number <= '9') *number++ = ' ';
  return 0;
}

static void work(void) {
  for (volatile unsigned int i = 0; i < 1000000; ++i) {}
}

int main(int argc, char **argv) {
  CHECK(argc >= 2);
  char path[PATH_MAX];
  if (!strcmp(argv[1], "normal")) { work(); return 0; }
  if (!strcmp(argv[1], "execveat-fd") || !strcmp(argv[1], "execveat-memfd")) {
    int source = open("/bin/true", O_RDONLY | O_CLOEXEC), fd = source;
    CHECK(source >= 0);
    if (!strcmp(argv[1], "execveat-memfd")) {
      fd = memfd_create("durante-execveat-fixture", 0);
      CHECK(fd >= 0);
      char buffer[8192];
      ssize_t count;
      while ((count = read(source, buffer, sizeof(buffer))) > 0)
        CHECK(write(fd, buffer, (size_t)count) == count);
      CHECK(count == 0 && fchmod(fd, 0700) == 0);
      close(source);
    }
    char *args[] = {"/bin/true", NULL};
    syscall(SYS_execveat, fd, "", args, environ, AT_EMPTY_PATH);
    return 3;
  }
  CHECK(own_report(path, sizeof(path)) == 0);
  if (!strcmp(argv[1], "fifo-stream") || !strcmp(argv[1], "symlink-stream") ||
      !strcmp(argv[1], "hardlink-stream")) {
    CHECK(unlink(path) == 0);
    if (!strcmp(argv[1], "fifo-stream")) CHECK(mkfifo(path, 0600) == 0);
    else {
      CHECK(argc == 3);
      if (!strcmp(argv[1], "symlink-stream")) CHECK(symlink(argv[2], path) == 0);
      else CHECK(link(argv[2], path) == 0);
    }
    char *args[] = {"/bin/true", NULL};
    execve(args[0], args, environ);
    return 3;
  }
  if (!strcmp(argv[1], "forge-records")) {
    pid_t parent = getpid(), child = fork();
    CHECK(child >= 0);
    if (!child) {
      /* Parent finalization precedes real exit. Rewriting its report after
       * ESRCH avoids relying on a race with the parent's final write. */
      const struct timespec delay = {0, 1000000};
      bool exited = false;
      for (unsigned int i = 0; i < 3000; ++i) {
        if (kill(parent, 0) < 0 && errno == ESRCH) { exited = true; break; }
        nanosleep(&delay, NULL);
      }
      CHECK(exited);
      int fd = open(path, O_RDWR | O_NOFOLLOW | O_CLOEXEC);
      CHECK(fd >= 0);
      char buffer[8192];
      ssize_t count = read(fd, buffer, sizeof(buffer) - 1);
      CHECK(count > 0 && count < (ssize_t)sizeof(buffer) - 1);
      buffer[count] = 0;
      CHECK(forge_counter(buffer, "instructions", '1') == 0);
      const char *fields[] = {"rep_bytes", "rep_entries", "unsupported_rep_entries", "signals_observed"};
      for (unsigned int i = 0; i < sizeof(fields) / sizeof(fields[0]); ++i)
        CHECK(forge_counter(buffer, fields[i], '0') == 0);
      CHECK(pwrite(fd, buffer, (size_t)count, 0) == count);
      CHECK(close(fd) == 0);
      return 0;
    }
    work();
    return 0;
  }
  return 2;
}
