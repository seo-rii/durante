/* Positive static group weight, bounded unsigned dynamic contribution. */
static bool add_weighted(uint64_t *total, uint64_t groups, unsigned weight) {
    if (groups > (UINT64_MAX - *total) / weight)
        return false;
    *total += groups * weight;
    return true;
}
