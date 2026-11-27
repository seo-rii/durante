<?php
$data = preg_split('/\s+/', trim(stream_get_contents(STDIN)));
$h = (int)$data[0];
$w = (int)$data[1];
$grid = implode('', array_slice($data, 2, $h));
$size = $h * $w;
$dist = array_fill(0, $size, -1);
$q = [];
if ($grid[0] === '.') { $dist[0] = 0; $q[] = 0; }
for ($head = 0; $head < count($q); $head++) {
    $cur = $q[$head];
    $d = $dist[$cur] + 1;
    $c = $cur % $w;
    $nb = $cur - $w;
    if ($nb >= 0 && $dist[$nb] < 0 && $grid[$nb] === '.') { $dist[$nb] = $d; $q[] = $nb; }
    $nb = $cur + $w;
    if ($nb < $size && $dist[$nb] < 0 && $grid[$nb] === '.') { $dist[$nb] = $d; $q[] = $nb; }
    $nb = $cur - 1;
    if ($c > 0 && $dist[$nb] < 0 && $grid[$nb] === '.') { $dist[$nb] = $d; $q[] = $nb; }
    $nb = $cur + 1;
    if ($c + 1 < $w && $dist[$nb] < 0 && $grid[$nb] === '.') { $dist[$nb] = $d; $q[] = $nb; }
}
echo $dist[$size - 1], ' ', count($q), "\n";
