<?php
$data = array_map('intval', preg_split('/\s+/', trim(stream_get_contents(STDIN))));
$n = $data[0];
$q = $data[1];
$a = array_slice($data, 2, $n);
sort($a);
$out = [];
for ($k = 0, $p = 2 + $n; $k < $q; $k++, $p++) {
    $x = $data[$p];
    $lo = 0;
    $hi = $n;
    while ($lo < $hi) {
        $mid = ($lo + $hi) >> 1;
        if ($a[$mid] <= $x) $lo = $mid + 1; else $hi = $mid;
    }
    $out[] = $lo;
}
echo implode("\n", $out), "\n";
