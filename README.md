# AI Revenue Recovery Agent

**Razorpay AI Buildathon — Track 03: AI Revenue Recovery**
Failure mode: Failed Subscription / Mandate Recovery

Built by Krushna Surve, 3rd Year Computer Engineering, Goa College of Engineering.

---

## Problem

Revenue loss from failed payments rarely happens as one clean event. A subscription payment fails, and the failure could mean anything — insufficient funds, an expired mandate, a revoked authorization, a bank-side error, an expired payment instrument, or a fraud/risk block. Each of these needs a *different* response. Retrying blindly wastes money and can violate compliance rules; not retrying at all leaves recoverable revenue on the table.

This project builds an agent that closes the loop: **detect the failure → diagnose the actual root cause → decide a bounded, compliant intervention → execute it → measure the outcome**, with a full audit trail.

---

## Architecture

A five-stage deterministic pipeline. No LLM is used anywhere in the detection, diagnosis, or decision path — every recovery decision is rule-based and fully explainable.

```
generate_data.py → diagnose.py → decide.py → execute.py → report.py
     |                  |             |            |            |
  batch.json      diagnosed.json  decided.json final_results.json  metrics + dashboard
                                       |
                                  audit_log.csv (built incrementally)
```

| Stage | File | What it does |
|---|---|---|
| 1. Data generation | `src/generate_data.py` | Generates a synthetic batch of 700 subscription payment records with realistic failure rates and reasons. Each record includes a hidden `ground_truth_recoverable` field, used **only** for final scoring — never read by the diagnosis or decision logic. |
| 2. Diagnosis | `src/diagnose.py` | Maps each failed payment's failure reason code to a root cause category, using deterministic rules only. |
| 3. Decision | `src/decide.py` | Maps each root cause to one of six interventions (retry, re-authorization request, payment-method-update request, win-back offer, manual review escalation, or abandonment). Enforces hard compliance stopping rules: a maximum of 3 retry attempts and a 7-day retry window, based on RBI/NPCI e-mandate retry rules. |
| 4. Execution | `src/execute.py` | Simulates executing the chosen intervention (no real payment APIs are called) and resolves the outcome — recovered, still failed, or correctly stopped — against the hidden ground truth. |
| 5. Reporting | `src/report.py` | Computes the final recovery metrics, generates a chart, and builds the interactive dashboard. |

### Why no database or API

This is a batch pipeline processing a fixed dataset, not a live service. All data moves through plain JSON and CSV files. A database or REST API would add setup complexity without improving correctness — the actual judged criteria are diagnosis accuracy, compliant stopping rules, and measured recovery, none of which benefit from extra infrastructure.

### Ground-truth integrity

The `ground_truth_recoverable` and `ground_truth_recovery_window_days` fields exist solely to score the agent after the fact. They are read **only** inside `execute.py` (to resolve outcomes) and `report.py` (to compute metrics) — never inside `diagnose.py` or `decide.py`. This keeps the decision engine's output honest: it never "knows the answer" while deciding.

---

## How to run

```bash
python src/generate_data.py
python src/diagnose.py
python src/decide.py
python src/execute.py
python src/report.py
```

Each script reads the previous stage's output and writes its own. After running all five, open `dashboard.html` directly in a browser (no server needed) to view the interactive report.

---

## Results (current run)

| Metric | Value |
|---|---|
| Total records processed | 700 |
| Total failed | 75 (10.7%) |
| Revenue at risk | ₹1,31,825 |
| Revenue recovered | ₹35,777 |
| Overall recovery rate (by amount) | 27.1% |
| **Recovery rate among ground-truth-recoverable cases** | **53.1%** — the more meaningful measure, since 20 of the 75 failures were never recoverable by design |
| Correctly stopped (compliant non-retry) | 20 |
| Still failed | 32 |
| Diagnosis errors | 0 |
| Wasted retry cost | ₹195 (13 attempts) |
| Missed recoverable revenue (aged past 7-day window) | ₹31,581 (19 cases) |
| Net recovery value | ₹35,582 |

**Why the overall rate looks lower than it is:** 20 of the 75 failures were correctly *not* retried at all — revoked mandates, risk-blocked accounts, and cases past the compliance retry window. These aren't missed opportunities; retrying them would be either pointless or non-compliant. The recoverable-only rate (53.1%) reflects what the decision engine actually achieved on cases that could genuinely be recovered.

**Compliance tradeoff:** 19 recoverable cases (₹31,581) were deliberately not retried because they exceeded the 7-day e-mandate retry window. This is a designed tradeoff — the system respects the compliance boundary even at the cost of some recoverable revenue, rather than retrying indefinitely.

---

## Dashboard

`dashboard.html` is a single self-contained file (no server, no build step) with:
- KPI summary cards (at-risk, recovered, recovery rate, net value)
- Outcome-by-failure-reason and financial flow charts
- A root-cause breakdown matrix
- A **Lifecycle Trace Inspector** — search any subscription ID to see its full trail: detected issue → diagnosed cause → chosen action → outcome
- A filterable, searchable audit log table
- A file-upload option to load a different batch dataset

---

## Optional: Q&A assistant (`src/ask.py`)

A read-only command-line assistant that answers natural-language questions about the finalized results.

```bash
python src/ask.py "Which failure reason had the lowest recovery rate?"
```

**This is optional and not required to run or evaluate the core system.** It only reads `output/audit_log.csv` and `output/metrics_summary.json` — it never imports, calls, or influences `diagnose.py`, `decide.py`, `execute.py`, or `report.py`.

### API key setup

This feature requires a Gemini or Claude API key, since it makes a live LLM call to answer your question. It is **not included in this repository** for obvious security reasons.

1. Create a `.env` file in the project root (already excluded via `.gitignore`)
2. Add one of:
   ```
   GEMINI_API_KEY=your-key-here
   ```
   or
   ```
   ANTHROPIC_API_KEY=your-key-here
   ```
3. Run the command above

If no key is set, the script exits cleanly with setup instructions instead of crashing — the rest of the project runs completely independently of this feature.

Use `--dry-run` to inspect the constructed prompt without making an API call:
```bash
python src/ask.py --dry-run "your question here"
```

---

## Known limitations

- Diagnosis is rule-based, not probabilistic — ambiguous or multi-cause failures aren't modeled
- Execution is simulated; no real payment gateway or bank API is called
- Runs as a single batch pass, not a live/asynchronous scheduler
- Sample sizes for rarer failure categories (e.g., risk_block) are small in this batch
- The optional Q&A assistant requires an external API key to run live

---

## Repository structure

```
/src     — all pipeline scripts
/data    — generated and intermediate data files
/output  — audit log, metrics, chart, dashboard
/docs    — architecture notes
```
