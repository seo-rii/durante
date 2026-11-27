use strict;
use warnings;

my @data = split ' ', do { local $/; <STDIN> };
my ($n, $q) = @data[0, 1];
my @a = sort { $a <=> $b } @data[2 .. $n + 1];
my @out;
for my $x (@data[$n + 2 .. $n + $q + 1]) {
    my ($lo, $hi) = (0, $n);
    while ($lo < $hi) {
        my $mid = ($lo + $hi) >> 1;
        if ($a[$mid] <= $x) { $lo = $mid + 1 } else { $hi = $mid }
    }
    push @out, $lo;
}
print join("\n", @out), "\n";
