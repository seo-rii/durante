data = STDIN.read.split.map!(&:to_i)
n, q = data[0], data[1]
a = data[2, n].sort!
out = data[2 + n, q].map { |x| a.bsearch_index { |v| v > x } || n }
STDOUT.write(out.join("\n") << "\n")
