<?php
$data = array_map('intval', preg_split('/\s+/', trim(stream_get_contents(STDIN))));
$n = $data[0];
$q = $data[1];
$bit = array_fill(0, $n + 1, 0);
for ($i = 1; $i <= $n; $i++) {
    $bit[$i] += $data[1 + $i];
    $j = $i + ($i & -$i);
    if ($j <= $n) $bit[$j] += $bit[$i];
}
$out = [];
$p = 2 + $n;
for ($k = 0; $k < $q; $k++) {
    $t = $data[$p];
    $x = $data[$p + 1];
    $y = $data[$p + 2];
    $p += 3;
    if ($t === 1) {
        for (; $x <= $n; $x += $x & -$x) $bit[$x] += $y;
    } else {
        $s = 0;
        for (; $y > 0; $y -= $y & -$y) $s += $bit[$y];
        for ($x--; $x > 0; $x -= $x & -$x) $s -= $bit[$x];
        $out[] = $s;
    }
}
echo implode("\n", $out), "\n";
