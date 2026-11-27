use strict;
use warnings;

my ($a, $b) = split ' ', do { local $/; <STDIN> };
my @a = unpack 'C*', $a;
my @b = unpack 'C*', $b;
my $m = @b;
my @prev = (0) x ($m + 1);
for my $ca (@a) {
    my @cur = (0);
    for my $j (1 .. $m) {
        if ($ca == $b[$j - 1]) {
            $cur[$j] = $prev[$j - 1] + 1;
        } else {
            my ($x, $y) = ($prev[$j], $cur[$j - 1]);
            $cur[$j] = $x > $y ? $x : $y;
        }
    }
    @prev = @cur;
}
print "$prev[$m]\n";
