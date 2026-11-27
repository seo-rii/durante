<?php
$data = array_map('intval', preg_split('/\s+/', trim(stream_get_contents(STDIN))));
$n = $data[0];
$m = $data[1];
$adj = array_fill(0, $n + 1, []);
for ($i = 2, $end = 2 + 3 * $m; $i < $end; $i += 3) {
    $adj[$data[$i]][] = $data[$i + 1];
    $adj[$data[$i]][] = $data[$i + 2];
}
$inf = PHP_INT_MAX;
$dist = array_fill(0, $n + 1, $inf);
$dist[1] = 0;
// SplPriorityQueue is a max-heap: use negated distances.
$pq = new SplPriorityQueue();
$pq->setExtractFlags(SplPriorityQueue::EXTR_BOTH);
$pq->insert(1, 0);
$reach = 0;
$total = 0;
while (!$pq->isEmpty()) {
    $top = $pq->extract();
    $u = $top['data'];
    $d = -$top['priority'];
    if ($d !== $dist[$u]) continue;
    $reach++;
    $total += $d;
    $e = $adj[$u];
    for ($j = 0, $c = count($e); $j < $c; $j += 2) {
        $v = $e[$j];
        $nd = $d + $e[$j + 1];
        if ($nd < $dist[$v]) {
            $dist[$v] = $nd;
            $pq->insert($v, -$nd);
        }
    }
}
echo ($dist[$n] < $inf ? $dist[$n] : -1), " $reach $total\n";
