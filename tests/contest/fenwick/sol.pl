use strict;
use warnings;

my @data = split ' ', do { local $/; <STDIN> };
my ($n, $q) = @data[0, 1];
my @bit = (0) x ($n + 1);
for my $i (1 .. $n) {
    $bit[$i] += $data[1 + $i];
    my $j = $i + ($i & -$i);
    $bit[$j] += $bit[$i] if $j <= $n;
}
my @out;
my $p = 2 + $n;
for (1 .. $q) {
    my ($t, $x, $y) = @data[$p .. $p + 2];
    $p += 3;
    if ($t == 1) {
        while ($x <= $n) { $bit[$x] += $y; $x += $x & -$x; }
    } else {
        my $s = 0;
        while ($y > 0) { $s += $bit[$y]; $y -= $y & -$y; }
        $x--;
        while ($x > 0) { $s -= $bit[$x]; $x -= $x & -$x; }
        push @out, $s;
    }
}
print join("\n", @out), "\n";
