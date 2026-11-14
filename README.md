# Back-office agents for a multi-company group

A working prototype of AI agents that run the month-end administration, bookkeeping and reporting for a group of companies on different systems, with the safeguards that work needs: agents propose, a written policy decides, and payments, bank-detail changes and anything suspicious always go to a person.

It is evaluated on 12 synthetic companies with five bank-statement formats, four accounting-export formats, their own charts of accounts and mailboxes, and planted fraud and prompt-injection attempts. Results: [docs/EVALUATION.md](docs/EVALUATION.md).

![Architecture](docs/architecture.png)

## What it does

| Agent | Input | Output |
|---|---|---|
| Inbox | Shared mailbox (`.eml`, PDF attachments) | Classifies each email; extracts and checks supplier invoices; turns requests into tasks; blocks bank-change requests, impersonation, lookalike senders and documents carrying hidden instructions |
| Bookkeeping | Bank statement, ledger export, invoices | Matches payments to invoices (short payments, combined payments, mistyped or missing references); categorises transactions from company history first, the matched invoice second, the language model last |
| Reporting | Categorised month | Management report PDF per company: figures computed in code, commentary written by the model and checked number by number |

Everything goes through `control.py`: an approval policy per company, a store per company and a hash-chained audit log. `api.py` is the approval web app (portfolio view, decision queue, audit trail) with company-scoped roles.

## Layout

```
backoffice/   connectors.py  each company's export formats -> one canonical model
              guards.py      redaction, injection screen, IBAN checksum, lookalike domains, invoice checks, number checker
              llm.py         OpenAI-compatible API, in-process open-weight model, per-company response cache
              agents.py      inbox, matching, categorisation, report commentary
              control.py     approval policy, per-company store, audit chain
              reporting.py   management report figures and PDF
              pipeline.py    month-end close for one company
              api.py         approval web app
synth/        synthetic 12-company data generator (all names invented)
eval/         scoring against ground truth, figure, report
tests/        unit tests (parsers, guards, matching, policy, audit, tenant isolation, web access)
runs/final/   recorded model responses and outputs of the evaluated run
```

## Run it

```bash
pip install -r requirements.txt
python synth/generate.py --out data                        # regenerate the 12 companies
python -m pytest                                            # unit tests

# Re-run the evaluated close offline from the recorded model responses
python -m backoffice.cli close --companies data/companies/C* --llm cache --model Qwen/Qwen2.5-7B-Instruct --var var --cache-dir runs/final_v2/llm_cache --llm-for-all
python eval/evaluate.py --runs var --split test

# Live, with any OpenAI-compatible endpoint (hosted API, vLLM, Ollama)
export BACKOFFICE_LLM_BASE_URL=http://localhost:8000/v1
python -m backoffice.cli close --companies data/companies/C04 --llm openai --model Qwen/Qwen2.5-7B-Instruct --var var

# Approval web app
BACKOFFICE_VAR=var BACKOFFICE_SECRET=$(openssl rand -hex 32) uvicorn backoffice.api:app --port 8080
```

## Executive assistant across Microsoft 365, Google, Zoho and cPanel

`assistant/` is one assistant working across several email and calendar systems. Each system has its own connector behind the
same small interface (`assistant/providers/`); triage, fraud checks, the calendar logic and drafting are shared.

| System | Connector | Writes |
|---|---|---|
| Microsoft 365 | Microsoft Graph (`graph.py`), mailbox access limited by Exchange RBAC for Applications | reply drafts, tentative holds, categories |
| Google Workspace | Gmail and Calendar APIs (`gmail.py`), internal OAuth app | reply drafts, tentative holds, labels |
| Zoho | Zoho Mail and Calendar APIs (`zoho.py`), Australian data centre | reply drafts, holds |
| cPanel or any other host | IMAP and CalDAV (`imap_caldav.py`) | reply drafts (IMAP APPEND), tentative holds, keywords |

Nothing is sent, accepted or paid. Replies are saved as threaded drafts in each account's own Drafts folder; meetings go in as tentative
holds after a clash check across all calendars; suspicious mail is held with no draft. Tone comes from examples of the owner's own sent
mail in that account, chosen per draft; no model is trained on the mail.

Demo (`python -m assistant.demo`): an invented director with four businesses on the four systems, 35 inbound emails (four planted attacks),
24 sent emails and 8 calendar events. The cPanel account runs on real local IMAP (pymap) and CalDAV (Radicale) servers; the Microsoft 365,
Google and Zoho accounts run the same adapter code against local stand-ins of each documented API (`assistant/standins.py`).
Results are in `runs/assistant_demo/` (first run) and `runs/assistant_demo_v2/` (second run, after general fixes to faults the first run
exposed on the same emails), including the rendered morning brief (`brief_p1.png`, `brief_p2.png`).

```bash
python -m assistant.demo --llm cache --out runs/assistant_demo_v2     # offline re-run from recorded model responses
python -m assistant.brief runs/assistant_demo_v2                      # morning brief HTML
```

## Status

Prototype on synthetic data. Production work would add official API connectors (accounting systems, Microsoft Graph or Gmail), single sign-on, PostgreSQL, a secrets manager, scheduling and monitoring, as set out in the solution design.
