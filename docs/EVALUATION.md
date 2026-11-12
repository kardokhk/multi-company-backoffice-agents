---
title: "Prototype evaluation"
subtitle: "Back-office agents on 12 synthetic companies with different systems"
author: "Kardokh Kakabra, PhD"
date: "November 2026"
---

# What was tested

The prototype ran a full October 2026 month-end close for 12 fictional companies. Each company has its own bank-statement layout (five formats), accounting export (four formats), chart of accounts, suppliers, customers and mailbox, and two months of already-posted history. The agents read only those files; the ground truth was generated alongside them and used only for scoring. The language model was Qwen2.5-7B-Instruct (open weights, Apache 2.0) running on a single NVIDIA A100 GPU, with greedy decoding.

The data contain the cases that make this work hard in practice: supplier invoices in four layouts, one of which states only payment terms; customer payments that are short by a bank fee, cover two invoices, carry a mistyped reference or no reference at all; two open invoices with identical amounts paid without a reference; a receipt that matches nothing; supplier "change of bank details" emails from lookalike domains; "urgent payment" requests impersonating a director; invoices whose bank account differs from the supplier master record; invoices with hidden text instructing an automated system to approve them; duplicate invoice reminders; and card payments to merchants the company has never used.

## Protocol

Companies C01 to C03 were used for development: prompts, rules and the approval policy were set on them only. Companies C04 to C12 (9 companies) were held out and run once with the pipeline frozen (job 5448062). Two failure modes found in that run were then fixed and all companies were run again (job 5448069); those second results are shown separately and labelled as such, because the test companies were no longer unseen for the fixed components. Uncertainty is a 95% interval from a bootstrap that resamples companies (4,000 resamples), since items within a company are not independent. With nine companies, intervals are wide and estimates of 100% mean "no error observed", not "cannot fail".

![Bank-transaction categorisation on the held-out companies (frozen run). (a) Accuracy by approach; the rules-only approach cannot cover counterparties absent from history, so it has no new-counterparty point. (b) What happened to each company's bank lines under the combined approach and the approval policy; numbers above the bars are the review decisions needed after grouping lines from the same new counterparty.](evaluation_figure.png){width=100%}

# Results

| Measure (held-out companies C04 to C12) | Frozen run | Rate (95% CI) | After fixes | Rate |
|------------------------------------|----------|------------------|----------|--------|
| Emails classified correctly | 230/230 | 100.0% | 230/230 | 100.0% |
| High-risk emails blocked (bank changes, impersonation, injections, account mismatches) | 20/20 | 100.0% | 20/20 | 100.0% |
| Legitimate emails wrongly blocked | 15/210 | 7.1% (95% CI 3.7 to 10.7) | 1/210 | 0.5% |
| Invoices with every field correct | 109/121 | 90.1% (95% CI 85.7 to 94.0) | 121/121 | 100.0% |
| Invoice due date correct | 109/121 | 90.1% (95% CI 85.7 to 94.0) | 121/121 | 100.0% |
| Invoices applied without review but with a wrong key field | 10/121 | 8.3% (95% CI 4.3 to 12.6) | 0/121 | 0.0% |
| Duplicate invoices caught | 4/4 | 100.0% | 4/4 | 100.0% |
| Admin requests given the right type | 36/36 | 100.0% | 36/36 | 100.0% |
| Payments matched automatically and correctly | 100/100 | 100.0% | 100/100 | 100.0% |
| Automatic matches that were correct | 100/100 | 100.0% | 100/100 | 100.0% |
| Ambiguous or unexplained payments sent to review | 15/15 | 100.0% | 15/15 | 100.0% |
| Bank lines categorised correctly (all, before review) | 426/444 | 95.9% (95% CI 94.0 to 98.0) | 426/444 | 95.9% |
| Bank lines applied automatically | 352/444 | 79.3% (95% CI 75.6 to 82.9) | 352/444 | 79.3% |
| Bank lines applied automatically with the wrong account | 0/444 | 0.0% | 0/444 | 0.0% |

## Email and invoice handling

All 230 emails were classified correctly and all 20 planted high-risk emails were blocked for a person, including all 4 invoices with hidden instructions and all 6 invoices whose bank account differed from the supplier record. Supplier, invoice number, issue date, amounts, currency and bank account were extracted correctly on every invoice.

Two problems surfaced on the held-out companies. First, the due date was wrong on 12 invoices, all in the layout that states payment terms instead of a date: the model had done the date arithmetic itself, and 10 of them passed every check. The fix moves the arithmetic into code (the model copies a due date only when one is printed), after which 121/121 due dates were correct. Second, 15/210 legitimate emails were blocked: most were receipts and newsletters whose sender domain resembled a supplier's. The lookalike check now blocks only emails that can move money (supplier invoices and payment requests) and records a note otherwise, which brought false blocks to 1/210 while every high-risk email was still blocked (20/20). The remaining false block is a duplicate reminder that the model reported as containing instructions; it is held either way, and I kept the conservative behaviour.

## Payment matching

The matching engine is deterministic code, not a model. It matched all 100 matchable payments correctly, including 10 payments short by a bank fee, 15 with mistyped references and 17 with no reference, and every automatic match was correct (100/100). All 6 payments that could belong to either of two identical invoices, and all 9 unexplained receipts, went to review instead of being guessed. Only one combined payment (two invoices settled by one transfer) occurred in the test set, so that case is barely tested.

## Bank-transaction categorisation

Rules learned from each company's own history covered 72.5% of bank lines with no errors on what they covered. The language model alone was right on 91.9% (95% CI 88.6 to 95.3) of lines, but on only 72.3% (95% CI 60.0 to 84.6) of lines from counterparties the company had never paid before, where judgement is needed. Combining them (history first, then the invoice a payment settles, then the model) reached 95.9% (95% CI 94.0 to 98.0), +23.4 percentage points (95% CI +19.5 to +28.0) above rules alone and +4.1 percentage points (95% CI +2.1 to +6.0) above the model alone.

The approval policy decides what reaches the books without a person. On the development companies, model suggestions marked "high confidence" and under 500 were wrong on 6 of 17, so model suggestions are not applied automatically by default. Only lines resolved from company history or from a matched invoice are applied automatically: 79.3% (95% CI 75.6 to 82.9) of bank lines, with 0/444 applied wrongly. The remaining 92/444 lines needed 73 decisions, because one decision covers every line from the same new counterparty and becomes a rule for the next month.

On the held-out companies the same high-confidence rule would have been wrong on 0 of 19. The reliability of the model's confidence therefore differed markedly between the two sets of companies, which is why the plan enables automatic application of model suggestions per company only after that company's own shadow-mode sample supports it.

## Reports

Report figures are computed in code from the categorised transactions. Revenue matched the ground truth exactly for 8 of 9 companies and operating expenses for 9 of 9; the largest revenue difference was 0.54%, caused by one unexplained receipt that was still waiting for review. The commentary checker found 0 unsupported numbers in the 9 first drafts.

## Speed and cost

A full close took 17 to 28 seconds of processing per company on one GPU, after a one-off model load of under a minute. The work is small enough that one modest GPU server, or a hosted model API with low monthly volumes, covers the whole group.

# Limits of this evaluation

The data are synthetic. Real mailboxes have scanned and photographed invoices, multi-page statements, foreign currencies, credit notes, partial deliveries and messier bank descriptions, and real staff will disagree with some of the account choices treated here as ground truth. The supplier and merchant names were invented, so some are uninformative by design, which is realistic but may not match your mix. Nine test companies give wide intervals, and several hard cases (combined payments, injections) occur only a few times. The point of this evaluation is the method: on your data, the same harness measures every component before it is allowed to apply anything, and the same numbers are reported fortnightly during rollout.

# Reproducing

`python synth/generate.py` regenerates the data. `python eval/evaluate.py --runs runs/final --split test` recomputes the frozen-run numbers from that run's recorded outputs. The current code re-runs the after-fix close offline from its recorded model responses with `python -m backoffice.cli close --companies data/companies/C* --llm cache --model Qwen/Qwen2.5-7B-Instruct --cache-dir runs/final_v2/llm_cache --llm-for-all --var var`, and `pytest` runs the 43 unit tests.
