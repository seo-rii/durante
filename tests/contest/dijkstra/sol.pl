use strict;
use warnings;

my @data = split ' ', do { local $/; <STDIN> };
my ($n, $m) = @data[0, 1];
my @adj;
for (my $i = 2; $i < 2 + 3 * $m; $i += 3) {
    push @{ $adj[$data[$i]] }, $data[$i + 1], $data[$i + 2];
}
my $inf = 1 << 62;
my @dist = ($inf) x ($n + 1);
$dist[1] = 0;
my @hd = (0);
my @hv = (1);
my ($reach, $total) = (0, 0);
while (@hd) {
    my ($d, $u) = ($hd[0], $hv[0]);
    my ($ld, $lv) = (pop @hd, pop @hv);
    my $size = @hd;
    if ($size) {
        my $k = 0;
        while (1) {
            my $c = 2 * $k + 1;
            last if $c >= $size;
            $c++ if $c + 1 < $size && $hd[$c + 1] < $hd[$c];
            last if $hd[$c] >= $ld;
            ($hd[$k], $hv[$k]) = ($hd[$c], $hv[$c]);
            $k = $c;
        }
        ($hd[$k], $hv[$k]) = ($ld, $lv);
    }
    next if $d != $dist[$u];
    $reach++;
    $total += $d;
    my $e = $adj[$u] or next;
    for (my $j = 0; $j < @$e; $j += 2) {
        my ($v, $nd) = ($e->[$j], $d + $e->[$j + 1]);
        next unless $nd < $dist[$v];
        $dist[$v] = $nd;
        my $k = @hd;
        while ($k > 0) {
            my $pa = ($k - 1) >> 1;
            last if $hd[$pa] <= $nd;
            ($hd[$k], $hv[$k]) = ($hd[$pa], $hv[$pa]);
            $k = $pa;
        }
        ($hd[$k], $hv[$k]) = ($nd, $v);
    }
}
print(($dist[$n] < $inf ? $dist[$n] : -1), " $reach $total\n");
