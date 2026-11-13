/* Inserted ONLY into a separate test SDK by runtime_exit_faults.py. Never part
 * of the production runtime patch or build recipe. all_threads_synch_lock
 * serializes these hooks. Count only completed cleanup, not attempted syncs.
 */
static int durante_test_cleaned VAR_IN_SECTION(NEVER_PROTECTED_SECTION) = 0;
extern bool durante_test_exiting;

static bool
durante_test_exit_fault_selected(const char *operation)
{
    const char *selected = getenv("DURANTE_TEST_EXIT_FAILURE");
    const char *after = getenv("DURANTE_TEST_EXIT_AFTER");
    return durante_test_exiting && selected != NULL && after != NULL &&
        strcmp(selected, operation) == 0 &&
        (strcmp(after, "0") == 0 || strcmp(after, "1") == 0) &&
        durante_test_cleaned == after[0] - '0';
}

static bool
durante_test_exit_fault(const char *operation)
{
    if (!durante_test_exit_fault_selected(operation))
        return false;
    print_file(STDERR, "<durante-test-exit-fault:%s:cleaned=%d>\n",
               operation, durante_test_cleaned);
    return true;
}
