#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int fallthrough(unsigned);
int split(unsigned);
int mixed(unsigned, unsigned);

static void stop(int number) {
    _exit(number == SIGFPE ? 0 : 1);
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--partial") == 0) {
        const unsigned prior[] = {1, 2, 1, 2, 1};
        if (signal(SIGFPE, stop) == SIG_ERR)
            return 1;
        for (unsigned i = 0; i < sizeof(prior) / sizeof(prior[0]); ++i)
            if (mixed(prior[i], 100))
                return 1;
        mixed(10, 3);
        return 1; /* Only the handler completes this case successfully. */
    }
    for (int i = 1; i < argc; ++i) {
        char *end;
        unsigned long length = strtoul(argv[i], &end, 10);
        if (*end || length == 0 || length > 1000 ||
            fallthrough((unsigned)length) || split((unsigned)length))
            return 1;
    }
    return 0;
}
