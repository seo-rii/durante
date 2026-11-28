# Measuring Java contest CPU time

The collector compiles the selected contest problem once per Java profile,
then records native, Durante and large-budget runs in each round. It uses the
standard corpus's JVM flags, full input generator, output oracle, supervisor
commands and CPU affinity. Supply an SDK containing the patched runtime
and its matching client. The original collector and aggregation formula
come from [the Java timing study](https://github.com/seo-rii/durante/blob/76b01e2f3fddf437e0e2bcbe6f3998c4c5dd85f5/experiments/java_timing/run.py).


The [archived Java 21 samples](results.json) cover seven contest problems and
five rounds on a four-vCPU Intel Xeon host using CPUs 0 and 1. The ordinary
Java profile's geometric mean CPU overhead, calculated from each problem's
five-run median, was 19.94× native with the historical `main` runtime/client
pair and 8.22× with the historical `both` pair. Those labels refer to the
binary hashes in the archive. Each large-budget run used four times that
round's measured work. The collection measured completed executions and did
not measure decisions near a contest time limit. The existing summarizer
reproduces the [published aggregates](summary.json) byte for byte.
[Original measurement](https://github.com/seo-rii/durante/blob/1e377e82b3763a798c2c66a8474c8cbd852b91e9/experiments/java_timing/results.json)

```sh
study=experiments/java_timing
python3 -B "$study/run.py" --output results.json --rounds 2 --cpus 0,1 \
  --runner-bin target/release/durante --config main="$SDK,$CLIENT" \
  --lang java=main --lang java-cds=main --problem bfs_grid
python3 -B "$study/summarize.py" results.json --write summary.json --markdown
```

Native runs are shared by all configurations within one profile, problem
and round. The summarizer checks the problem set from the input hashes, the
configurations selected for each profile, and every zero-based round index
before computing CPU-time minima, medians or geometric means. A partial
checkpoint can be inspected in `results.json`. A summary requires one
successful, correctly answered run in each declared cell for every round.
