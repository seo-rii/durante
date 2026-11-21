#include <signal.h>
#include <string.h>
#include <unistd.h>

int fallthrough(unsigned count);
int split(unsigned count);
int partial(void);

static void stop(int signal_number) {
    _exit(signal_number == SIGFPE ? 0 : 1);
}

int main(int argc, char **argv) {
    const unsigned lengths[] = {1, 1, 3, 2};
    if (argc > 1) {
        if (argc != 2 || strcmp(argv[1], "--partial") != 0 ||
            signal(SIGFPE, stop) == SIG_ERR)
            return 1;
        return partial();
    }
    for (unsigned i = 0; i < sizeof(lengths) / sizeof(lengths[0]); ++i)
        if (fallthrough(lengths[i]) || split(lengths[i]))
            return 1;
    return 0;
}
