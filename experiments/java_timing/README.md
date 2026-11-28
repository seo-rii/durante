# Measuring Java contest CPU time

The collector compiles the selected contest problem once per Java profile,
then records native, Durante and large-budget runs in each round. It uses the
standard corpus's JVM flags, full input generator, output oracle, supervisor
commands and CPU affinity. Supply an SDK containing the patched runtime
and its matching client. The original collector and aggregation formula
come from [the Java timing study](https://github.com/seo-rii/durante/blob/76b01e2f3fddf437e0e2bcbe6f3998c4c5dd85f5/experiments/java_timing/run.py).

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
