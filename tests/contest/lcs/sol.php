<?php
[$a, $b] = preg_split('/\s+/', trim(stream_get_contents(STDIN)));
$n = strlen($a);
$m = strlen($b);
$prev = array_fill(0, $m + 1, 0);
for ($i = 0; $i < $n; $i++) {
    $ca = $a[$i];
    $cur = [0];
    for ($j = 1; $j <= $m; $j++) {
        if ($ca === $b[$j - 1]) {
            $cur[$j] = $prev[$j - 1] + 1;
        } else {
            $x = $prev[$j];
            $y = $cur[$j - 1];
            $cur[$j] = $x > $y ? $x : $y;
        }
    }
    $prev = $cur;
}
echo $prev[$m], "\n";
