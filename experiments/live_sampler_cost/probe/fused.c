/* Compose the two registered clients in their original registration order.
 * The v11 parser uses argc. The span parser receives its usual -out argument. */
#include "dr_api.h"
#include <string.h>

void v11_main(client_id_t id, int argc, const char *argv[]);
void span_main(client_id_t id, int argc, const char *argv[]);

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
    if (argc < 3 || strcmp(argv[argc - 2], "-span_report") != 0) {
        dr_fprintf(STDERR, "sampler_fused: expected a final -span_report <new file>\n");
        dr_abort_with_code(1);
    }
    const char *span_args[] = {argv[0], "-out", argv[argc - 1], NULL};
    v11_main(id, argc - 2, argv);
    span_main(id, 3, span_args);
}
