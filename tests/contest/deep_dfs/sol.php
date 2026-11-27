<?php
// Plain recursion: the PHP VM executes userland calls without C recursion.
const MOD = 1000000007;
$data = array_map('intval', preg_split('/\s+/', trim(stream_get_contents(STDIN))));
$n = $data[0];
$adj = array_fill(0, $n + 1, []);
for ($i = 1, $end = 2 * $n - 1; $i < $end; $i += 2) {
    $adj[$data[$i]][] = $data[$i + 1];
    $adj[$data[$i + 1]][] = $data[$i];
}
$maxDepth = 0;
$total = 0;

function dfs(int $v, int $parent, int $depth): int {
    global $adj, $maxDepth, $total;
    if ($depth > $maxDepth) $maxDepth = $depth;
    $size = 1;
    foreach ($adj[$v] as $u) {
        if ($u !== $parent) $size += dfs($u, $v, $depth + 1);
    }
    $total = ($total + $size * $v) % MOD;
    return $size;
}

dfs(1, 0, 0);
echo "$maxDepth $total\n";
