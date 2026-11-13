#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

#define CHECK(value) do { if (!(value)) { \
    fprintf(stderr, "ioctl fixture failed at %d errno %d\n", __LINE__, errno); return 2; \
} } while (0)

int main(void) {
    int descriptors[2], pending = -1, nonblocking = 1;
    char bytes[2];
    CHECK(pipe2(descriptors, O_CLOEXEC) == 0);
    CHECK(write(descriptors[1], "query", 5) == 5);
    CHECK(ioctl(descriptors[0], FIONREAD, &pending) == 0 && pending == 5);
    CHECK(read(descriptors[0], bytes, sizeof(bytes)) == (ssize_t)sizeof(bytes));
    CHECK(ioctl(descriptors[0], FIONREAD, &pending) == 0 && pending == 3);
    CHECK(ioctl(descriptors[0], FIONCLEX) == 0);
    CHECK((fcntl(descriptors[0], F_GETFD) & FD_CLOEXEC) == 0);
    CHECK(ioctl(descriptors[0], FIOCLEX) == 0);
    CHECK((fcntl(descriptors[0], F_GETFD) & FD_CLOEXEC) != 0);
    CHECK(ioctl(descriptors[0], FIONBIO, &nonblocking) == -1 && errno == EPERM);
    CHECK((fcntl(descriptors[0], F_GETFL) & O_NONBLOCK) == 0);
    CHECK(ioctl(descriptors[0], TCGETS, bytes) == -1 && errno == EPERM);
    CHECK(ioctl(descriptors[0], TIOCSTI, bytes) == -1 && errno == EPERM);
    CHECK(close(descriptors[0]) == 0 && close(descriptors[1]) == 0);
    CHECK(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, descriptors) == 0);
    CHECK(write(descriptors[0], "query", 5) == 5);
    CHECK(ioctl(descriptors[1], FIONREAD, &pending) == 0 && pending == 5);
    CHECK(close(descriptors[0]) == 0 && close(descriptors[1]) == 0);
    puts("readonly-ioctl");
    return 0;
}
