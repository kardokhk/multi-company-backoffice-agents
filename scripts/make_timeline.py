"""Phased migration timeline (PDF + 300 dpi PNG). usage: python scripts/make_timeline.py docs/timeline"""
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 7})
BLUE, ORANGE, GREEN, SKY, VERM, GREY = "#0072B2", "#E69F00", "#009E73", "#56B4E9", "#D55E00", "#9aa1ab"
rows = [  # label, start week (1-based), end week, colour, note
    ("0  Discovery and baseline", 1, 2, GREY, "systems inventory, opportunity register, baseline hours"),
    ("1  Foundations", 2, 4, BLUE, "security, access, connectors and approval app for 2 pilots"),
    ("2  Pilot in shadow mode", 4, 6, SKY, "agents run alongside the team for one close"),
    ("3a Pilots in assisted mode", 6, 12, GREEN, "approval queue live; thresholds tuned from data"),
    ("3b Wave 1: same systems as pilots", 6, 8, ORANGE, "about 4 companies"),
    ("3c Wave 2: new connectors", 8, 10, ORANGE, "about 4 companies"),
    ("3d Wave 3: legacy and spreadsheets", 10, 12, ORANGE, "remaining companies"),
    ("4  Complex tasks, assist level", 9, 12, VERM, "intercompany, VAT and payroll preparation"),
    ("5  Handover", 11, 12, GREY, "runbooks, training, monitoring"),
]
fig, ax = plt.subplots(figsize=(160 / 25.4, 72 / 25.4))
for i, (lab, s, e, c, note) in enumerate(rows):
    y = len(rows) - 1 - i
    ax.barh(y, e - s + 1, left=s - 0.5, height=0.62, color=c, edgecolor="none")
ax.set_yticks(range(len(rows)))
ax.set_yticklabels([r[0] for r in rows][::-1], fontsize=7)
for wk, lab, ha in ((6, "pilot go / no-go", "center"), (12, "all companies in assisted mode", "right")):
    ax.axvline(wk + 0.5, color="#111", lw=0.6, ls=(0, (3, 2)))
    ax.text(wk + 0.5 + (0.1 if ha == "right" else 0), len(rows) - 0.35, lab, ha=ha, va="bottom", fontsize=6.5, fontweight="bold", bbox=dict(fc="white", ec="none", pad=1.2))
ax.set_xlim(0.5, 12.7)
ax.set_xticks(range(1, 13))
ax.set_xlabel("Week", fontsize=7)
ax.set_ylim(-0.6, len(rows) + 0.25)
for s in ("top", "right", "left"):
    ax.spines[s].set_visible(False)
ax.tick_params(axis="y", length=0)
ax.spines["bottom"].set_bounds(0.5, 12.5)
fig.tight_layout()
out = sys.argv[1] if len(sys.argv) > 1 else "docs/timeline"
fig.savefig(out + ".pdf")
fig.savefig(out + ".png", dpi=300)
print("wrote", out)
