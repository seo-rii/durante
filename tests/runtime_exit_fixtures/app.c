#include <fcntl.h>
#include <pthread.h>
#include <string.h>
#include <unistd.h>

__attribute__((noinline, visibility("default"))) int worker_checkpoint(void) {
    __asm__ volatile("" ::: "memory");
    return 17;
}

static void *worker(void *unused) {
    (void)unused;
    volatile int value = worker_checkpoint();
    (void)value;
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 4)
        return 2;
    alarm(5);
    pthread_t thread;
    if (pthread_create(&thread, NULL, worker, NULL))
        return 3;
    while (access(argv[2], F_OK) != 0)
        usleep(1000);
    if (!strcmp(argv[1], "join")) {
        int release = open(argv[3], O_WRONLY | O_CREAT | O_EXCL, 0600);
        if (release < 0 || close(release))
            return 4;
        if (pthread_join(thread, NULL))
            return 4;
    } else if (strcmp(argv[1], "exit")) {
        return 2;
    }
    _exit(0);
}
