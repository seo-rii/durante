"""Draw native timing spread and the effect of a first-input anchor on the eleven later inputs."""
import argparse
import gzip
import json
from pathlib import Path
from statistics import median

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FixedFormatter


def plot(data, output):
    timing = json.loads(gzip.decompress((data / "evaluation.json.gz").read_bytes()))["native_reference"]
    models = json.loads((data / "transfer.json").read_bytes())["within_elf"]
    names = list(models["first_native"]["cases"])
    fig, (native, estimate) = plt.subplots(1, 2, sharey=True, figsize=(10, 6.6),
                                           gridspec_kw={"width_ratios": [1, 1.2]})
    blue, orange, green = "#216a87", "#ad590e", "#196946"
    ticks = (0.5, 0.8, 1, 1.2, 2, 3)
    estimate.set_xscale("log")
    estimate.set_xlim(0.40, 3.7)
    estimate.xaxis.set_major_locator(FixedLocator(ticks))
    estimate.xaxis.set_major_formatter(FixedFormatter([f"{x:g}×" for x in ticks]))
    estimate.minorticks_off()
    estimate.axvspan(.8, 1.2, color="#d8f0e4")
    estimate.axvline(1, color=green, lw=1)
    native.set_xscale("log")
    native.set_xlim(1.3, 210)
    native.xaxis.set_major_locator(FixedLocator([2, 5, 10, 20, 50, 100, 200]))
    native.xaxis.set_major_formatter(FixedFormatter(["2", "5", "10", "20", "50", "100", "200"]))
    native.minorticks_off()
    for i, name in enumerate(names):
        times = [t / 1e6 for t in timing[name]["native_ns"]]
        m = median(times)
        native.plot([min(times), max(times)], [i, i], color=blue, lw=2)
        native.scatter(times, [i] * len(times), s=10, color=blue, alpha=.45)
        native.scatter(m, i, color=blue, s=24)
        for method, shift, color, marker in (("frozen", -.12, orange, "s"), ("first_native", .12, blue, "o")):
            ratios = [n / (m * 1e6) for n in models[method]["cases"][name]["predictions_ns"]]
            estimate.plot([min(ratios), max(ratios)], [i + shift] * 2, color=color, lw=2)
            estimate.scatter(median(ratios), i + shift, color=color, marker=marker, s=32, zorder=3)
    native.set_yticks(range(len(names)), names)
    native.set_ylim(len(names) - .4, -.7)
    for ax in (native, estimate):
        ax.axhline(7.5, color="#9aa6ab", lw=.8)
        ax.grid(axis="x", color="#c3cbd0", linewidth=.5, alpha=.6)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(axis="y", length=0)
        ax.set_axisbelow(True)
    native.set_title("Native CPU per execution", loc="left", fontsize=13)
    estimate.set_title("Prediction / native median", loc="left", fontsize=13)
    native.set_xlabel("Milliseconds (nine measured runs)")
    estimate.set_xlabel("Shaded band is within 20% of native")
    handles = [Line2D([], [], color=c, marker=m, linestyle="None", label=label) for c, m, label in
               ((orange, "s", "Frozen reference"), (blue, "o", "Same ELF, one native anchor"))]
    fig.suptitle("Timing later inputs after one native calibration", x=.17, ha="left",
                 fontsize=17, fontweight="bold")
    fig.legend(handles=handles, loc="lower center", ncol=2, frameon=False)
    fig.subplots_adjust(left=.17, right=.97, top=.87, bottom=.16, wspace=.12)
    fig.savefig(output, dpi=155)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    plot(args.data, args.output)
