# Transferring a frozen branch cost from affordable-item queries to inversion counting

Adding a frozen branch-miss penalty corrected the two Fenwick false timeouts
on both Milan boots. It also improved inversion-count decisions from 19/20
to 20/20 on each boot. On the predecessor panel it introduced two false
accepts of the multiset solver at 1.5×. Total agreement there stayed at
18/20. The preregistered criterion passed because its predecessor endpoint
targeted only the two Fenwick cases.

The predecessor panel reuses the six original ELF/input pairs. A budget buys
the most expensive remaining affordable item. The intended solver uses a
sorted array and disjoint sets. Its multiset and Fenwick alternatives run on
the same inventory in the original uniform and clustered query orders.
Earlier observations put both Fenwick implementations below the native
reference's time and above its original ML4 score. [Predecessor study](../predecessor/README.md)

Inversion counting has appeared in Aonohako. This held-out comparison uses
three new implementations and two new inputs. The reference counts strict
inversions during merge sort. The alternatives maintain rank frequencies
in a Fenwick tree or an iterative segment tree. Both inputs contain the
same 262,144 integer ranks, including duplicates. Every successive window
of 512 ranks is alternately sorted ascending or descending in the second
input. Changing their order preserves the multiset and changes the correct
answer. An independent oracle counts each inversion at the most significant
bit where its two ranks differ. Its output was 17,210,817,667 for the shuffled
input and 17,210,794,250 for the reordered one. [Solvers](inversions.cpp) ·
[Generator and oracle](inputs.py)

## Frozen models and verdicts

Each score is expressed in nanoseconds. For every model and input the
predicted ratio is the submission's score divided by the reference solver's
score using that same model. The native ratio uses the submission's native
CPU time and the same reference's native CPU time. An inclusive relative
limit accepts equality. All reported decisive results cover every cross-pair
of observed endpoints, including when pooling both boots. The comparison
uses completed runs and their simulated decisions.

- **ML4** multiplies the original eight Dijkstra-weighted features by their
  frozen coefficients in seconds and scales by one billion.
- **gshare** uses the complete independent fit: 0.11400208036234295 ns per
  instruction, 0.03515017240371441 ns per REP byte, zero per load and
  6.92658480142496 ns per simulated misprediction.
- **ML4 plus misses** adds 6.92658480142496 ns per simulated misprediction
  to the ML4 score. The independently fitted coefficient estimates the
  marginal miss cost alongside the instruction and REP terms. Applying it
  to an existing latency/cache score tests whether it transfers and can
  double-count costs present in ML4. This study fitted no coefficients.

The protocol froze the 1×, 1.25×, 1.5×, 2× and 3× boundaries before either
guest was created. The primary candidate had to correct both old Fenwick
false timeouts on both new boots. On the inversion holdout it had to preserve
every old native-decisive agreement and lower the median absolute relative
ratio error independently on both boots. [Preregistration](https://github.com/seo-rii/durante/commit/b410405414138ea67f79d40ed7d8a6db1541aa3c) · [Selection rules](protocol.json) · [Independent branch fit](../milan/gshare/README.md)

## Results

Pooled submission/reference time ratios are shown below. Individual-boot
native ranges, errors and decisions for every threshold are in the
[full replay](results.json).

| Input / submission | Native median | ML4 | gshare | ML4 plus misses |
|---|---:|---:|---:|---:|
| uniform-multiset | 1.592× | 1.803× | 1.048× | 1.346× |
| uniform-fenwick | 0.872× | 1.062× | 0.936× | 0.923× |
| clustered-multiset | 1.711× | 1.820× | 1.060× | 1.394× |
| clustered-fenwick | 0.932× | 1.071× | 0.942× | 0.933× |
| shuffled-fenwick | 0.621× | 0.838× | 0.580× | 0.591× |
| shuffled-segtree | 0.905× | 0.797× | 0.899× | 0.937× |
| runs-fenwick | 0.706× | 0.869× | 0.701× | 0.739× |
| runs-segtree | 1.181× | 0.825× | 1.063× | 1.141× |

For uniform predecessor queries, Fenwick's original ML4 score exceeded
DSU's by 706,933 ns while it incurred 341,546 fewer simulated misses.
Correcting the 1× decision requires ≥2.0698 ns per saved miss. The
clustered case requires ≥2.6244 ns. The multiset also incurs fewer
simulated misses than DSU. Applying the same 6.9266 ns penalty brought
both multiset ratios below the 1.5× deadline despite native timeouts on
both boots. Standalone gshare produced four false accepts on this panel.

On inversions, block sorting changed segment-tree/merge native time from
0.905× to 1.181×. ML4 predicted 0.797× and 0.825× respectively. The
reference's simulated misses fell from 2.67M to 1.61M, while the segment
tree's fell from 2.93M to 2.76M. The combined score ratio rose from
0.937× to 1.141× and recovered the 1× timeout. Across four holdout
submission/input ratios the pooled median absolute relative error was
26.56% for ML4, 3.67% for standalone gshare and 4.05% for ML4 plus misses.
Both branch models agreed on all 20 holdout decisions. Those 20 deadlines
come from four submission/input pairs within one problem family and two
query orders sharing the same multiset. Each model had the
same count of agreements and errors on the two individual boots.

All 288 processes returned the expected output. Steal was zero on both
guest CPUs. For each of the 12 program/input pairs the full-cache arms
matched on every decoded work and cache field. Branch counts were identical
in all four readings. Pooled median overhead ranged from 43.63–102.26×
native for flat full-cache, 42.95–93.09× for the branch arm and
86.58–195.35× for collecting both. The two VMs and their boot disks
returned HTTP 404 after removal.

**Retrospective coefficient bound.** Using the observed endpoints of these
two panels after unblinding, the single penalty satisfying every
native-decisive cell is strictly between 3.07893 and 3.54116 ns/miss
(bounds rounded for display). The reordered segment tree at 1× sets the
lower endpoint, and the uniform predecessor multiset at 1.5× sets the
upper endpoint. The exact fractions are in the replay. This interval is a
hypothesis for an independent corpus. Earlier set-Dijkstra evidence already
bounds the relative score below 2× for any nonnegative mixture of its
frozen ML4 score and predicted misses. [Earlier branch-transfer bound](../milan/gshare/README.md)

## Reproduction

The archive pairs native CPU, the original full cache probe, its flat
equivalent, and the gshare sidecar on the registered ELF and input. The last
two execute in separate processes. Their summed CPU ratios describe
collecting both features in two passes. The gshare predictor has 14 history
bits with a one-bit PC shift. These are software predictor events over a
fixed address layout and CPUID profile. Raw measurements come from guest
CPU 1 on two independent SMT-off Milan VMs. The schedule includes seven
native runs, one original full-cache run, two optimized full-cache runs and
two branch-profile runs per solver/input per boot. [Guest lifecycle](evidence/cloud.json)

From the repository root using Python 3.11, NumPy and a C++20 compiler:

```sh
study=experiments/capacity_misses/order_statistics_transfer
python3 -B "$study/replay.py" --verify "$study/results.json"
python3 -B -m unittest discover -s "$study"
```

Recreate the sealed guest archive at the registration revision using GCC
12.2.0, the original unpacked predecessor payload and the two previously
published diagnostic binaries. The generator hashes the compiled code, full
oracle output and original guest runtime before packaging.

```sh
python3 -B "$study/prepare.py" --predecessor "$PREDECESSOR_PAYLOAD" \
  --flat "$FLAT_CACHE_CLIENT" --branch "$GSHARE_CLIENT" --output "$NEW_PAYLOAD"
cmp "$NEW_PAYLOAD.registration.json" "$study/registration.json"
```
