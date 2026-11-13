/* Two live application threads let the runtime clean one before an injected failure. */
#include <fcntl.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <string.h>
#include <unistd.h>

static atomic_int ready, stop;

static void *worker(void *unused) {
    (void)unused;
    atomic_fetch_add(&ready, 1);
    while (!atomic_load(&stop))
        sched_yield();
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 3)
        return 2;
    alarm(5);
    pthread_t threads[2];
    for (int i = 0; i < 2; ++i)
        if (pthread_create(&threads[i], NULL, worker, NULL))
            return 3;
    while (atomic_load(&ready) != 2)
        sched_yield();
    int marker = open(argv[2], O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (marker < 0 || write(marker, "2\n", 2) != 2 || close(marker))
        return 4;
    if (!strcmp(argv[1], "join")) {
        atomic_store(&stop, 1);
        for (int i = 0; i < 2; ++i)
            if (pthread_join(threads[i], NULL))
                return 4;
    } else if (strcmp(argv[1], "exit")) {
        return 2;
    }
    _exit(0);
}
