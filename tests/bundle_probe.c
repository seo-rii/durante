/* Fixed deployment smoke: expose actual loaded-object paths, then exit normally. */
#include <fcntl.h>
#include <unistd.h>

int main(void) {
    int fd = open("/proc/self/maps", O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return 1;
    char buffer[4096];
    unsigned total = 0;
    for (;;) {
        ssize_t got = read(fd, buffer, sizeof(buffer));
        if (got < 0)
            return 2;
        if (!got)
            break;
        total += (unsigned)got;
        if (total > 65536)
            return 3;
        ssize_t offset = 0;
        while (offset < got) {
            ssize_t written = write(STDOUT_FILENO, buffer + offset, (size_t)(got - offset));
            if (written <= 0)
                return 4;
            offset += written;
        }
    }
    return close(fd) != 0;
}
