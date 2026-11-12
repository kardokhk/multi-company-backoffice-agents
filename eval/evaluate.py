"""Score a close run against the synthetic ground truth.

usage: python eval/evaluate.py --runs runs/test_v1 --truth data/truth --split test --out runs/test_v1/metrics.json

Uncertainty: the company is the sampling unit (items within a company are not independent), so every
proportion gets a 95% percentile interval from a cluster bootstrap that resamples companies.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backoffice.agents import counterparty_key  # noqa: E402
from backoffice.connectors import load_tenant  # noqa: E402
from backoffice.control import DEFAULT_POLICY, txn_decision  # noqa: E402

SEED, B = 20261112, 4000
HIGH = {"prompt_injection", "bank_details_mismatch"}


def norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def boot(per_company: dict[str, tuple[float, float]]):
    """per_company: cid -> (numerator, denominator). Returns pooled estimate and 95% CI."""
    ids = [c for c, (_, d) in per_company.items() if d > 0]
    if not ids:
        return {"est": None, "lo": None, "hi": None, "num": 0, "den": 0}
    num = np.array([per_company[c][0] for c in ids], float)
    den = np.array([per_company[c][1] for c in ids], float)
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(ids), size=(B, len(ids)))
    bs = num[idx].sum(1) / den[idx].sum(1)
    return {"est": float(num.sum() / den.sum()), "lo": float(np.percentile(bs, 2.5)), "hi": float(np.percentile(bs, 97.5)),
            "num": int(num.sum()), "den": int(den.sum()), "companies": len(ids)}


def boot_diff(a: dict, b: dict):
    """Paired difference of pooled proportions a - b, resampling the same companies for both."""
    ids = [c for c in a if a[c][1] > 0 and b.get(c, (0, 0))[1] > 0]
    na, da = (np.array([a[c][i] for c in ids], float) for i in (0, 1))
    nb, db = (np.array([b[c][i] for c in ids], float) for i in (0, 1))
    rng = np.random.default_rng(SEED + 1)
    idx = rng.integers(0, len(ids), size=(B, len(ids)))
    d = na[idx].sum(1) / da[idx].sum(1) - nb[idx].sum(1) / db[idx].sum(1)
    return {"est": float(na.sum() / da.sum() - nb.sum() / db.sum()), "lo": float(np.percentile(d, 2.5)), "hi": float(np.percentile(d, 97.5))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--truth", default="data/truth")
    ap.add_argument("--data", default="data/companies")
    ap.add_argument("--split", default="test")
    ap.add_argument("--out")
    a = ap.parse_args()

    C = defaultdict(lambda: defaultdict(lambda: [0, 0]))   # metric -> company -> [num, den]
    confusion = Counter()
    errors = defaultdict(list)
    sweep = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))  # policy -> cid -> [auto, auto_wrong, total]
    report_err = {}
    timing = {}
    commentary = {}

    for tf in sorted(Path(a.truth).glob("*/truth.json")):
        T = json.loads(tf.read_text())
        if a.split != "all" and T["split"] != a.split:
            continue
        cid = T["company"]
        rp = Path(a.runs) / cid / "run.json"
        if not rp.exists():
            print("missing run for", cid)
            continue
        R = json.loads(rp.read_text())
        t = load_tenant(Path(a.data) / cid)
        timing[cid] = R["seconds"]
        kinds = {x.code: x.kind for x in t.accounts}

        def add(metric, ok, n=1):
            C[metric][cid][0] += ok
            C[metric][cid][1] += n

        # ------------------------------------------------ emails
        pred = {e["message_id"]: e for e in R["emails"]}
        for te in T["emails"]:
            pe = pred[te["message_id"]]
            add("email_class_accuracy", pe["category"] == te["class"])
            add(f"email_recall_{te['class']}", pe["category"] == te["class"])
            confusion[(te["class"], pe["category"])] += 1
            if pe["category"] != te["class"]:
                errors["email_class"].append(f"{cid} {te['message_id']} truth={te['class']} pred={pe['category']} subj={pe['subject']!r}")
            blocked = bool(set(pe["flags"]) & {"payment_risk", "prompt_injection", "lookalike_sender_domain", "bank_details_mismatch"}) \
                or pe["category"] == "payment_risk"
            risky = te["class"] == "payment_risk" or bool(set(te["flags"]) & HIGH)
            if risky:
                add("risk_recall", blocked)
                if not blocked:
                    errors["risk_missed"].append(f"{cid} {te['message_id']} {te['flags']} {pe['subject']!r}")
                for f in set(te["flags"]) & HIGH:
                    add(f"risk_recall_{f}", f in pe["flags"] or blocked)
                if te["class"] == "payment_risk":
                    add("risk_recall_payment_request", blocked)
            else:
                add("false_block_rate", blocked)
                if blocked:
                    errors["false_block"].append(f"{cid} {te['message_id']} flags={pe['flags']} {pe['subject']!r}")
            if "duplicate" in te["flags"]:
                add("duplicate_recall", "duplicate" in pe["flags"])
            elif te["class"] == "supplier_invoice":
                add("duplicate_false_rate", "duplicate" in pe["flags"])
            if te["class"] == "admin_request":
                add("admin_request_type_accuracy", (pe.get("request") or {}).get("type") == te.get("request_type"))
            if te["class"] == "supplier_invoice":
                inv = pe.get("invoice") or {}
                f = te["fields"]
                checks = {
                    "supplier": norm(inv.get("supplier")) == norm(f["supplier"]) or norm(inv.get("supplier_master")) == norm(f["supplier"]),
                    "invoice_number": norm(inv.get("invoice_number")) == norm(f["invoice_number"]),
                    "issue_date": inv.get("issue_date") == f["issue_date"],
                    "due_date": inv.get("due_date") == f["due_date"],
                    "currency": (inv.get("currency") or "").upper() == f["currency"],
                    "net": inv.get("net") is not None and abs(inv["net"] - f["net"]) <= 0.01,
                    "tax": inv.get("tax") is not None and abs(inv["tax"] - f["tax"]) <= 0.01,
                    "total": inv.get("total") is not None and abs(inv["total"] - f["total"]) <= 0.01,
                    "bank_account": norm(inv.get("bank_account")) == norm(f["bank_account"]),
                }
                for k, ok in checks.items():
                    add(f"field_{k}", ok)
                    if not ok:
                        errors["field"].append(f"{cid} {te['message_id']} {k}: truth={f.get(k if k != 'tax' else 'tax')!r} pred={inv.get(k)!r}")
                add("invoice_all_fields_correct", all(checks.values()))
                key_ok = all(checks[k] for k in ("invoice_number", "issue_date", "due_date", "total"))
                status_auto = not (set(pe["flags"]) & ({"payment_risk", "prompt_injection", "lookalike_sender_domain", "bank_details_mismatch",
                                                        "duplicate", "amounts_do_not_add_up", "unreadable_amounts", "unreadable_dates",
                                                        "due_before_issue", "issue_date_in_future", "foreign_currency", "missing_invoice_number",
                                                        "missing_total", "bank_account_fails_checksum", "new_supplier", "unreadable_model_output"}))
                add("invoice_straight_through", status_auto)
                add("invoice_silent_error", status_auto and not key_ok)
                if status_auto and not key_ok:
                    errors["silent_invoice_error"].append(f"{cid} {te['message_id']} {[k for k, v in checks.items() if not v]}")

        # ------------------------------------------------ reconciliation
        ap_msg = {}
        for te in T["emails"]:
            if te["class"] == "supplier_invoice" and "duplicate" not in te["flags"]:
                ap_msg[te["invoice_id"]] = te["message_id"]
        ar_num = {x["invoice_id"]: x["number"] for x in T["ar_invoices"]}
        mapid = lambda i: ar_num.get(i) or ap_msg.get(i)
        M = R["matches"]
        truth_lines = {r["txn_id"]: r for r in T["reconciliation"]}
        for tid, r in truth_lines.items():
            m = M.get(tid)
            want = sorted(mapid(i) for i in r["invoices"])
            if r["expect"] == "auto":
                ok = bool(m) and m["status"] == "auto" and sorted(m["invoice_ids"]) == want
                add("match_correct_auto", ok)
                add(f"match_case_{r['case']}", ok)
            else:
                ok = bool(m) and m["status"] == "review"
                add("match_flagged_for_review", ok)
                add(f"match_case_{r['case']}", ok)
            if not ok:
                errors["match"].append(f"{cid} {tid} case={r['case']} want={want} got={m and (m['status'], m['rule'], m['invoice_ids'])}")
        for tid, m in M.items():
            if m["status"] == "auto":
                r = truth_lines.get(tid)
                ok = bool(r) and r["expect"] == "auto" and sorted(m["invoice_ids"]) == sorted(mapid(i) for i in r["invoices"])
                add("match_auto_precision", ok)
                if not ok:
                    errors["match_false_auto"].append(f"{cid} {tid} {m['rule']} {m['invoice_ids']}")

        # ------------------------------------------------ categorisation
        truth_acct = {x["txn_id"]: x["account_code"] for x in T["transactions"]}
        mem = defaultdict(Counter)
        for p in t.history:
            mem[counterparty_key(p.description)][p.account_code] += 1
        txn = {x.txn_id: x for x in t.bank}
        for tid, want in truth_acct.items():
            x = txn[tid]
            key = counterparty_key(x.description)
            new = key not in mem
            c = mem.get(key)
            rule = c.most_common(1)[0][0] if c and c.most_common(1)[0][1] / sum(c.values()) >= 0.8 else None
            add("cat_rules_only_accuracy", rule == want)
            add("cat_rules_only_coverage", rule is not None)
            llm = (R["llm_all"].get(tid) or {}).get("account_code")
            add("cat_model_only_accuracy", llm == want)
            P = R["postings"][tid]
            add("cat_system_accuracy", P["account_code"] == want)
            stratum = "new_counterparty" if new else "known_counterparty"
            add(f"cat_system_accuracy_{stratum}", P["account_code"] == want)
            add(f"cat_model_only_accuracy_{stratum}", llm == want)
            add("cat_straight_through", P["status"] == "auto_applied")
            add("cat_auto_error", P["status"] == "auto_applied" and P["account_code"] != want)
            if P["status"] == "auto_applied":
                add("cat_auto_precision", P["account_code"] == want)
            if P["account_code"] != want:
                errors["categorisation"].append(f"{cid} {tid} {x.description!r} {x.amount:+.2f} want={want} got={P['account_code']} "
                                                f"src={P['source']} conf={P.get('confidence')} status={P['status']}")
            # policy sweep for model-sourced proposals
            if P["source"] == "model":
                for confs in (("high",), ("high", "medium")):
                    for cap in (100, 250, 500, 1000, 1e9):
                        st, _ = txn_decision(P, x.amount, {"model_auto_confidence": list(confs), "model_auto_max_amount": cap})
                        s = sweep[f"{'+'.join(confs)}|{cap:g}"][cid]
                        s[0] += st == "auto_applied"
                        s[1] += st == "auto_applied" and P["account_code"] != want
                        s[2] += 1
        pend = [P for P in R["postings"].values() if P["status"] == "pending"]
        add("cat_review_lines", len(pend), len(truth_acct))
        add("cat_review_decisions", len({P.get("group") or id(P) for P in pend}), len(truth_acct))
        # ------------------------------------------------ report figures vs truth
        def totals(codes):
            rev = sum(txn[t_].amount for t_, c in codes.items() if kinds.get(c) == "income")
            opx = -sum(txn[t_].amount for t_, c in codes.items() if kinds.get(c) == "expense")
            return rev, opx
        tr, to = totals(truth_acct)
        pr, po = totals({k: v["account_code"] for k, v in R["postings"].items()})
        report_err[cid] = {"revenue_truth": round(tr, 2), "revenue_report": round(pr, 2), "opex_truth": round(to, 2), "opex_report": round(po, 2),
                           "revenue_abs_pct_error": round(100 * abs(pr - tr) / tr, 2) if tr else None,
                           "opex_abs_pct_error": round(100 * abs(po - to) / to, 2) if to else None}
        ch = R["commentary_check"]
        commentary[cid] = {"unsupported_first_draft": len(ch["unsupported_first_draft"]), "retried": ch["retried"],
                           "sentences_dropped": ch["sentences_dropped"], "unsupported_final": len(ch["unsupported_final"])}

    metrics = {k: boot({c: tuple(v) for c, v in d.items()}) for k, d in sorted(C.items())}
    diffs = {
        "system_minus_rules_only_accuracy": boot_diff({c: tuple(v) for c, v in C["cat_system_accuracy"].items()},
                                                      {c: tuple(v) for c, v in C["cat_rules_only_accuracy"].items()}),
        "system_minus_model_only_accuracy": boot_diff({c: tuple(v) for c, v in C["cat_system_accuracy"].items()},
                                                      {c: tuple(v) for c, v in C["cat_model_only_accuracy"].items()}),
    }
    sw = {}
    for pol, d in sweep.items():
        auto = sum(v[0] for v in d.values()); wrong = sum(v[1] for v in d.values()); tot = sum(v[2] for v in d.values())
        sw[pol] = {"model_items": tot, "auto": auto, "auto_wrong": wrong, "auto_share": auto / tot if tot else None,
                   "error_among_auto": wrong / auto if auto else None}
    per_company = {k: {c: list(v) for c, v in d.items()} for k, d in sorted(C.items())}
    res = {"split": a.split, "runs": a.runs, "companies": sorted(timing), "metrics": metrics, "per_company": per_company, "differences": diffs, "policy_sweep": sw,
           "email_confusion": {f"{k[0]}->{k[1]}": v for k, v in sorted(confusion.items())}, "report_figures": report_err,
           "commentary_check": commentary, "seconds_per_company": timing, "errors": {k: v for k, v in errors.items()}}
    out = Path(a.out or Path(a.runs) / f"metrics_{a.split}.json")
    out.write_text(json.dumps(res, indent=1))
    for k, v in metrics.items():
        if v["est"] is not None:
            print(f"{k:45s} {v['est']:.3f} [{v['lo']:.3f}, {v['hi']:.3f}]  {v['num']}/{v['den']}")
    for k, v in diffs.items():
        print(f"{k:45s} {v['est']:+.3f} [{v['lo']:+.3f}, {v['hi']:+.3f}]")
    for k, v in sorted(sw.items()):
        print(f"sweep {k:22s} auto {v['auto']}/{v['model_items']}  wrong {v['auto_wrong']}")
    for k, v in errors.items():
        print(f"--- {k}: {len(v)}")
        for e in v[:40]:
            print("   ", e)
    print(json.dumps(commentary))
    print(json.dumps(report_err))


if __name__ == "__main__":
    main()
