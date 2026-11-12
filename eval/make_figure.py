"""Evaluation figure from a metrics file (PDF + 300 dpi PNG).

usage: python eval/make_figure.py runs/final/metrics_test.json docs/evaluation_figure
"""
import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 7, "axes.linewidth": 0.6})
BLUE, ORANGE, GREEN, VERM, GREY = "#0072B2", "#E69F00", "#009E73", "#D55E00", "#9aa1ab"
M = json.load(open(sys.argv[1]))
m, pc = M["metrics"], M["per_company"]

fig, (a, b) = plt.subplots(1, 2, figsize=(183 / 25.4, 70 / 25.4), gridspec_kw={"width_ratios": [1, 1.25]})
arms = [("Rules from company\nhistory only", "cat_rules_only_accuracy", None),
        ("Language model\nonly", "cat_model_only_accuracy", "cat_model_only_accuracy_new_counterparty"),
        ("Combined: history,\ninvoice match, model", "cat_system_accuracy", "cat_system_accuracy_new_counterparty")]
for i, (lab, k_all, k_new) in enumerate(arms):
    v = m[k_all]
    a.errorbar(v["est"] * 100, i + 0.12, xerr=[[100 * (v["est"] - v["lo"])], [100 * (v["hi"] - v["est"])]], fmt="o", color=BLUE, ms=4,
               capsize=2, lw=0.8, label="all bank lines" if i == 0 else None)
    a.text(v["hi"] * 100 + 1.2, i + 0.12, f"{100 * v['est']:.1f}%", va="center", fontsize=6.5, color=BLUE)
    if k_new:
        w = m[k_new]
        a.errorbar(w["est"] * 100, i - 0.18, xerr=[[100 * (w["est"] - w["lo"])], [100 * (w["hi"] - w["est"])]], fmt="s", color=ORANGE,
                   ms=3.5, capsize=2, lw=0.8, label="new counterparties" if i == 1 else None)
        a.text(w["hi"] * 100 + 1.2, i - 0.18, f"{100 * w['est']:.1f}%", va="center", fontsize=6.5, color=ORANGE)
a.set_yticks(range(3))
a.set_yticklabels([x[0] for x in arms])
a.set_xlim(50, 108)
a.set_ylim(2.5, -0.5)
a.set_xticks([50, 60, 70, 80, 90, 100])
a.set_xlabel("Categorisation accuracy (%, 95% CI)")
a.legend(frameon=False, loc="upper right", fontsize=6.3, handletextpad=0.3)
for s in ("top", "right"):
    a.spines[s].set_visible(False)


cids = sorted(pc["cat_straight_through"])
tot = [pc["cat_straight_through"][c][1] for c in cids]
auto = [pc["cat_straight_through"][c][0] for c in cids]
wrong = [pc["cat_auto_error"][c][0] for c in cids]
dec = [pc["cat_review_decisions"][c][0] for c in cids]
lines = [pc["cat_review_lines"][c][0] for c in cids]
x = range(len(cids))
ok = [100 * (u - w) / t for u, w, t in zip(auto, wrong, tot)]
bad = [100 * w / t for w, t in zip(wrong, tot)]
rev = [100 * (t - u) / t for u, t in zip(auto, tot)]
b.bar(x, ok, color=GREEN, width=0.7, label="applied automatically, correct")
b.bar(x, bad, bottom=ok, color=VERM, width=0.7, label="applied automatically, wrong")
b.bar(x, rev, bottom=[p + q for p, q in zip(ok, bad)], color=GREY, width=0.7, label="sent to a person")
for i, (l, d) in enumerate(zip(lines, dec)):
    b.text(i, 101.5, f"{d}", ha="center", va="bottom", fontsize=6)
b.set_title("Number above each bar: review decisions needed", fontsize=6.5, color="#333", pad=10)
b.set_xticks(list(x))
b.set_xticklabels(cids)
b.set_ylabel("Bank lines (%)")
b.set_ylim(0, 112)
b.set_yticks([0, 25, 50, 75, 100])
fig.legend(*b.get_legend_handles_labels(), frameon=False, loc="lower center", bbox_to_anchor=(0.72, 0.0), ncol=3, fontsize=6.3,
           handlelength=1, columnspacing=1)
for s in ("top", "right"):
    b.spines[s].set_visible(False)

fig.tight_layout(w_pad=2.5, rect=(0, 0.07, 1, 1))
fig.text(0.01, 0.97, "a", fontsize=8, fontweight="bold")
fig.text(0.50, 0.97, "b", fontsize=8, fontweight="bold")
out = sys.argv[2]
fig.savefig(out + ".pdf")
fig.savefig(out + ".png", dpi=300)
print("wrote", out)
