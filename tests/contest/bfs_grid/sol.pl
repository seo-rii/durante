use strict;
use warnings;

my @data = split ' ', do { local $/; <STDIN> };
my ($h, $w) = @data[0, 1];
my $grid = join '', @data[2 .. $h + 1];
my $size = $h * $w;
my @dist = (-1) x $size;
my @q;
if (substr($grid, 0, 1) eq '.') { $dist[0] = 0; push @q, 0; }
my $head = 0;
while ($head < @q) {
    my $cur = $q[$head++];
    my $d = $dist[$cur] + 1;
    my $c = $cur % $w;
    for my $nb ($cur >= $w ? $cur - $w : (), $cur + $w < $size ? $cur + $w : (),
                $c > 0 ? $cur - 1 : (), $c + 1 < $w ? $cur + 1 : ()) {
        next if $dist[$nb] >= 0 || substr($grid, $nb, 1) ne '.';
        $dist[$nb] = $d;
        push @q, $nb;
    }
}
print "$dist[$size - 1] ", scalar(@q), "\n";
