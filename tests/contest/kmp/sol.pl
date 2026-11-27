use strict;
use warnings;

my ($t, $p) = split ' ', do { local $/; <STDIN> };
my @t = unpack 'C*', $t;
my @p = unpack 'C*', $p;
my $m = @p;
my @fail = (0) x $m;
my $k = 0;
for my $i (1 .. $m - 1) {
    $k = $fail[$k - 1] while $k > 0 && $p[$i] != $p[$k];
    $k++ if $p[$i] == $p[$k];
    $fail[$i] = $k;
}
my ($count, $total) = (0, 0);
$k = 0;
for my $i (0 .. $#t) {
    my $ch = $t[$i];
    $k = $fail[$k - 1] while $k > 0 && $ch != $p[$k];
    $k++ if $ch == $p[$k];
    if ($k == $m) { $count++; $total += $i - $m + 2; $k = $fail[$k - 1]; }
}
print "$count $total\n";
