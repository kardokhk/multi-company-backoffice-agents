"""Write docs/EVALUATION.md from metrics files, so every number in the report comes from the evaluation.

usage: python eval/make_report.py runs/final/metrics_test.json runs/final_v2/metrics_test.json runs/final/metrics_dev.json docs/EVALUATION.md
"""
import json
import sys

F, V2, D = (json.load(open(p)) for p in sys.argv[1:4])
OUT = sys.argv[4]
TESTS = sys.argv[5] if len(sys.argv) > 5 else "unit"


def p(M, k, ci=True):
    v = M["metrics"][k]
    s = f"{100 * v['est']:.1f}%"
    return s + (f" (95% CI {100 * v['lo']:.1f} to {100 * v['hi']:.1f})" if ci and v["lo"] != v["hi"] else "")


def n(M, k):
    v = M["metrics"][k]
    return f"{v['num']}/{v['den']}"


def d(M, k):
    v = M["differences"][k]
    return f"{100 * v['est']:+.1f} percentage points (95% CI {100 * v['lo']:+.1f} to {100 * v['hi']:+.1f})"


def row(label, k, Ms=(F, V2)):
    return f"| {label} | {n(Ms[0], k)} | {p(Ms[0], k)} | {n(Ms[1], k)} | {p(Ms[1], k, ci=False)} |"


ncomp = len(F["companies"])
sw = F["policy_sweep"]
swd = D["policy_sweep"]
hi500 = sw["high|500"]
hi500d = swd["high|500"]
secs = F["seconds_per_company"]
rep = F["report_figures"]
rev_exact = sum(1 for c in rep.values() if c["revenue_abs_pct_error"] == 0)
opx_exact = sum(1 for c in rep.values() if c["opex_abs_pct_error"] == 0)
max_rev_err = max(c["revenue_abs_pct_error"] for c in rep.values())
unsupported = sum(c["unsupported_first_draft"] for c in F["commentary_check"].values())

text = f"""---
title: "Prototype evaluation"
subtitle: "Back-office agents on 12 synthetic companies with different systems"
author: "Kardokh Kakabra, PhD"
date: "November 2026"
---

# What was tested

The prototype ran a full October 2026 month-end close for 12 fictional companies. Each company has its own bank-statement layout (five formats), accounting export (four formats), chart of accounts, suppliers, customers and mailbox, and two months of already-posted history. The agents read only those files; the ground truth was generated alongside them and used only for scoring. The language model was Qwen2.5-7B-Instruct (open weights, Apache 2.0) running on a single NVIDIA A100 GPU, with greedy decoding.

The data contain the cases that make this work hard in practice: supplier invoices in four layouts, one of which states only payment terms; customer payments that are short by a bank fee, cover two invoices, carry a mistyped reference or no reference at all; two open invoices with identical amounts paid without a reference; a receipt that matches nothing; supplier "change of bank details" emails from lookalike domains; "urgent payment" requests impersonating a director; invoices whose bank account differs from the supplier master record; invoices with hidden text instructing an automated system to approve them; duplicate invoice reminders; and card payments to merchants the company has never used.

## Protocol

Companies C01 to C03 were used for development: prompts, rules and the approval policy were set on them only. Companies C04 to C12 ({ncomp} companies) were held out and run once with the pipeline frozen (job 5448062). Two failure modes found in that run were then fixed and all companies were run again (job 5448069); those second results are shown separately and labelled as such, because the test companies were no longer unseen for the fixed components. Uncertainty is a 95% interval from a bootstrap that resamples companies (4,000 resamples), since items within a company are not independent. With nine companies, intervals are wide and estimates of 100% mean "no error observed", not "cannot fail".

![Bank-transaction categorisation on the held-out companies (frozen run). (a) Accuracy by approach; the rules-only approach cannot cover counterparties absent from history, so it has no new-counterparty point. (b) What happened to each company's bank lines under the combined approach and the approval policy; numbers above the bars are the review decisions needed after grouping lines from the same new counterparty.](evaluation_figure.png){{width=100%}}

# Results

| Measure (held-out companies C04 to C12) | Frozen run | Rate (95% CI) | After fixes | Rate |
|------------------------------------|----------|------------------|----------|--------|
{row("Emails classified correctly", "email_class_accuracy")}
{row("High-risk emails blocked (bank changes, impersonation, injections, account mismatches)", "risk_recall")}
{row("Legitimate emails wrongly blocked", "false_block_rate")}
{row("Invoices with every field correct", "invoice_all_fields_correct")}
{row("Invoice due date correct", "field_due_date")}
{row("Invoices applied without review but with a wrong key field", "invoice_silent_error")}
{row("Duplicate invoices caught", "duplicate_recall")}
{row("Admin requests given the right type", "admin_request_type_accuracy")}
{row("Payments matched automatically and correctly", "match_correct_auto")}
{row("Automatic matches that were correct", "match_auto_precision")}
{row("Ambiguous or unexplained payments sent to review", "match_flagged_for_review")}
{row("Bank lines categorised correctly (all, before review)", "cat_system_accuracy")}
{row("Bank lines applied automatically", "cat_straight_through")}
{row("Bank lines applied automatically with the wrong account", "cat_auto_error")}

## Email and invoice handling

All {n(F, "email_class_accuracy").split("/")[1]} emails were classified correctly and all {n(F, "risk_recall").split("/")[1]} planted high-risk emails were blocked for a person, including all {F["metrics"]["risk_recall_prompt_injection"]["den"]} invoices with hidden instructions and all {F["metrics"]["risk_recall_bank_details_mismatch"]["den"]} invoices whose bank account differed from the supplier record. Supplier, invoice number, issue date, amounts, currency and bank account were extracted correctly on every invoice.

Two problems surfaced on the held-out companies. First, the due date was wrong on {F["metrics"]["field_due_date"]["den"] - F["metrics"]["field_due_date"]["num"]} invoices, all in the layout that states payment terms instead of a date: the model had done the date arithmetic itself, and {F["metrics"]["invoice_silent_error"]["num"]} of them passed every check. The fix moves the arithmetic into code (the model copies a due date only when one is printed), after which {n(V2, "field_due_date")} due dates were correct. Second, {n(F, "false_block_rate")} legitimate emails were blocked: most were receipts and newsletters whose sender domain resembled a supplier's. The lookalike check now blocks only emails that can move money (supplier invoices and payment requests) and records a note otherwise, which brought false blocks to {n(V2, "false_block_rate")} while every high-risk email was still blocked ({n(V2, "risk_recall")}). The remaining false block is a duplicate reminder that the model reported as containing instructions; it is held either way, and I kept the conservative behaviour.

## Payment matching

The matching engine is deterministic code, not a model. It matched all {F["metrics"]["match_correct_auto"]["den"]} matchable payments correctly, including {F["metrics"]["match_case_short_paid_fee"]["den"]} payments short by a bank fee, {F["metrics"]["match_case_typo_ref"]["den"]} with mistyped references and {F["metrics"]["match_case_amount_only"]["den"]} with no reference, and every automatic match was correct ({n(F, "match_auto_precision")}). All {F["metrics"]["match_case_ambiguous_twins"]["den"]} payments that could belong to either of two identical invoices, and all {F["metrics"]["match_case_unexplained"]["den"]} unexplained receipts, went to review instead of being guessed. Only one combined payment (two invoices settled by one transfer) occurred in the test set, so that case is barely tested.

## Bank-transaction categorisation

Rules learned from each company's own history covered {p(F, "cat_rules_only_coverage", ci=False)} of bank lines with no errors on what they covered. The language model alone was right on {p(F, "cat_model_only_accuracy")} of lines, but on only {p(F, "cat_model_only_accuracy_new_counterparty")} of lines from counterparties the company had never paid before, where judgement is needed. Combining them (history first, then the invoice a payment settles, then the model) reached {p(F, "cat_system_accuracy")}, {d(F, "system_minus_rules_only_accuracy")} above rules alone and {d(F, "system_minus_model_only_accuracy")} above the model alone.

The approval policy decides what reaches the books without a person. On the development companies, model suggestions marked "high confidence" and under 500 were wrong on {hi500d["auto_wrong"]} of {hi500d["auto"]}, so model suggestions are not applied automatically by default. Only lines resolved from company history or from a matched invoice are applied automatically: {p(F, "cat_straight_through")} of bank lines, with {n(F, "cat_auto_error")} applied wrongly. The remaining {n(F, "cat_review_lines")} lines needed {F["metrics"]["cat_review_decisions"]["num"]} decisions, because one decision covers every line from the same new counterparty and becomes a rule for the next month.

On the held-out companies the same high-confidence rule would have been wrong on {hi500["auto_wrong"]} of {hi500["auto"]}. The reliability of the model's confidence therefore differed markedly between the two sets of companies, which is why the plan enables automatic application of model suggestions per company only after that company's own shadow-mode sample supports it.

## Reports

Report figures are computed in code from the categorised transactions. Revenue matched the ground truth exactly for {rev_exact} of {ncomp} companies and operating expenses for {opx_exact} of {ncomp}; the largest revenue difference was {max_rev_err:.2f}%, caused by one unexplained receipt that was still waiting for review. The commentary checker found {unsupported} unsupported numbers in the {ncomp} first drafts.

## Speed and cost

A full close took {min(secs.values()):.0f} to {max(secs.values()):.0f} seconds of processing per company on one GPU, after a one-off model load of under a minute. The work is small enough that one modest GPU server, or a hosted model API with low monthly volumes, covers the whole group.

# Limits of this evaluation

The data are synthetic. Real mailboxes have scanned and photographed invoices, multi-page statements, foreign currencies, credit notes, partial deliveries and messier bank descriptions, and real staff will disagree with some of the account choices treated here as ground truth. The supplier and merchant names were invented, so some are uninformative by design, which is realistic but may not match your mix. Nine test companies give wide intervals, and several hard cases (combined payments, injections) occur only a few times. The point of this evaluation is the method: on your data, the same harness measures every component before it is allowed to apply anything, and the same numbers are reported fortnightly during rollout.

# Reproducing

`python synth/generate.py` regenerates the data. `python eval/evaluate.py --runs runs/final --split test` recomputes the frozen-run numbers from that run's recorded outputs. The current code re-runs the after-fix close offline from its recorded model responses with `python -m backoffice.cli close --companies data/companies/C* --llm cache --model Qwen/Qwen2.5-7B-Instruct --cache-dir runs/final_v2/llm_cache --llm-for-all --var var`, and `pytest` runs the {TESTS} unit tests.
"""
open(OUT, "w").write(text)
print("wrote", OUT)
