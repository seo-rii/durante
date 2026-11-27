<?php
[$t, $p] = preg_split('/\s+/', trim(stream_get_contents(STDIN)));
$n = strlen($t);
$m = strlen($p);
$fail = array_fill(0, $m, 0);
for ($i = 1, $k = 0; $i < $m; $i++) {
    while ($k > 0 && $p[$i] !== $p[$k]) $k = $fail[$k - 1];
    if ($p[$i] === $p[$k]) $k++;
    $fail[$i] = $k;
}
$count = 0;
$total = 0;
for ($i = 0, $k = 0; $i < $n; $i++) {
    $ch = $t[$i];
    while ($k > 0 && $ch !== $p[$k]) $k = $fail[$k - 1];
    if ($ch === $p[$k]) $k++;
    if ($k === $m) { $count++; $total += $i - $m + 2; $k = $fail[$k - 1]; }
}
echo "$count $total\n";
