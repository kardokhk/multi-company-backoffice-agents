"""Architecture diagram (PDF + 300 dpi PNG). usage: python scripts/make_architecture.py docs/architecture"""
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 7})
BLUE, ORANGE, GREEN, GREY, RED, SKY = "#0072B2", "#E69F00", "#009E73", "#6b7280", "#D55E00", "#56B4E9"
W, H = 183 / 25.4, 112 / 25.4
fig = plt.figure(figsize=(W, H))
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 183)
ax.set_ylim(0, 112)
ax.axis("off")


def X(x):
    return 10 + (x - 8) * 0.985


def box(x, y, w, h, title, lines=(), edge=BLUE, fill="#ffffff", tsize=7.5, lsize=6.3):
    x, w = X(x), w * 0.985
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.6", lw=0.9, ec=edge, fc=fill))
    ax.text(x + w / 2, y + h - 3.2, title, ha="center", va="top", fontsize=tsize, fontweight="bold", color="#111")
    for i, l in enumerate(lines):
        ax.text(x + w / 2, y + h - 7.6 - i * 3.6, l, ha="center", va="top", fontsize=lsize, color="#222")


def arrow(x1, y1, x2, y2, color="#333", style="-|>", lw=0.8, ls="-"):
    ax.add_patch(FancyArrowPatch((X(x1), y1), (X(x2), y2), arrowstyle=style, mutation_scale=7, lw=lw, color=color, linestyle=ls))


def band(y, h, label):
    ax.add_patch(FancyBboxPatch((1, y), 181, h, boxstyle="round,pad=0,rounding_size=1.5", lw=0, fc="#f3f5f8"))
    ax.text(4.2, y + h / 2, label, fontsize=6, color=GREY, va="center", ha="center", rotation=90, fontweight="bold", linespacing=1.2)


band(84, 26, "COMPANY\nSYSTEMS")
band(46, 36, "AUTOMATION SERVICE\none workspace per company")
band(2, 42, "PEOPLE AND\nCONTROLS")

srcs = [("Accounting system", ["API or export", "Xero, QuickBooks, Sage,", "spreadsheets"]),
        ("Bank feeds", ["statements in any", "CSV or Excel layout"]),
        ("Mailboxes", ["invoices, receipts,", "requests (Graph / Gmail)"]),
        ("Shared drives", ["contracts, payroll", "inputs, templates"])]
for i, (t, l) in enumerate(srcs):
    box(8 + i * 43.5, 87, 38, 18, t, l, edge=GREY)

box(8, 66, 167, 11, "Connectors and canonical data model", ["one adapter per system and format; every company maps to the same records"], edge=BLUE, fill="#eef5fb")
for i in range(4):
    arrow(27 + i * 43.5, 87, 27 + i * 43.5, 77.6)

agents = [("Inbox agent", ["classify, extract invoice", "fields, create tasks"]),
          ("Bookkeeping agent", ["match payments, categorise", "from company memory"]),
          ("Reporting agent", ["compute figures in code,", "write checked commentary"])]
for i, (t, l) in enumerate(agents):
    box(8 + i * 41, 48.5, 36, 14.5, t, l, edge=ORANGE, fill="#fff8ec")
    arrow(26 + i * 41, 66, 26 + i * 41, 63.6)
box(133, 48.5, 42, 14.5, "Safeguards (all agents)", ["redaction, injection screen,", "checksums, lookalike domains"], edge=RED, fill="#fdf0ec")
arrow(133, 55.7, 125, 55.7, color=RED, style="<|-|>")

box(8, 26, 70, 14, "Approval policy (code, not the model)", ["auto-apply only what is measured as safe;", "payments and bank changes: always a person"], edge=GREEN, fill="#eefaf4")
for i in range(3):
    arrow(26 + i * 41, 48.5, 43, 40.6, color=GREY)
box(88, 26, 42, 14, "Approval queue", ["web app, role and", "company scoped"], edge=GREEN, fill="#eefaf4")
arrow(78, 33, 88, 33)
box(140, 26, 35, 14, "Write-back", ["drafts via official", "APIs, never payments"], edge=BLUE, fill="#eef5fb")
arrow(130, 33, 140, 33)
# write-back route along the right margin to the company systems
xr = X(175) + 3.4
ax.plot([X(175) + 0.4, xr, xr], [33, 33, 96], color=BLUE, lw=0.8, ls="--")
ax.add_patch(FancyArrowPatch((xr, 96), (X(175) + 0.9, 96), arrowstyle="-|>", mutation_scale=7, lw=0.8, color=BLUE))

box(8, 5, 52, 15, "Audit log", ["hash-chained, every proposal", "and decision, per company"], edge=GREY)
box(66, 5, 52, 15, "Monitoring", ["accuracy samples, exception", "rates, cost per item"], edge=GREY)
box(124, 5, 51, 15, "Reports", ["monthly pack per company", "and group view"], edge=GREY)
arrow(43, 26, 34, 20.6, color=GREY)

out = sys.argv[1] if len(sys.argv) > 1 else "docs/architecture"
fig.savefig(out + ".pdf")
fig.savefig(out + ".png", dpi=300)
print("wrote", out)
