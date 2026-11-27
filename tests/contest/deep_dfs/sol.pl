use strict;
use warnings;
no warnings 'recursion';   # Perl recursion is heap-allocated; only the warning needs silencing.

my $MOD = 1_000_000_007;
my @data = split ' ', do { local $/; <STDIN> };
my $n = $data[0];
my @adj;
for (my $i = 1; $i < 2 * $n - 1; $i += 2) {
    push @{ $adj[$data[$i]] }, $data[$i + 1];
    push @{ $adj[$data[$i + 1]] }, $data[$i];
}
my ($max_depth, $total) = (0, 0);

sub dfs {
    my ($v, $parent, $depth) = @_;
    $max_depth = $depth if $depth > $max_depth;
    my $size = 1;
    for my $u (@{ $adj[$v] }) {
        $size += dfs($u, $v, $depth + 1) if $u != $parent;
    }
    $total = ($total + $size * $v) % $MOD;
    return $size;
}

dfs(1, 0, 0);
print "$max_depth $total\n";
