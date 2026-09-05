"""
src/decide.py
-------------
Phase 3: Intervention Decision Engine for Failed Payments.

Reads /data/diagnosed.json and assigns a deterministic recovery action to every
failed record based on its failure_reason_code and retry attempt count.

HARD RULE: This file must NEVER read, reference, or import any field whose
name starts with "ground_truth_". Doing so would invalidate all metrics.

Intervention Mapping (failure_reason_code → chosen_action):
    insufficient_funds       → retry_scheduled
    mandate_expired          → reauth_request
    mandate_revoked          → no_retry_winback_offer
    bank_technical_decline   → immediate_retry_once
    instrument_expired       → update_payment_method_request
    risk_block               → manual_review_escalation

Stopping Rules (hard-coded constants):
    MAX_RETRY_ATTEMPTS = 3   — no more than 3 automated attempts per sub
    RETRY_WINDOW_DAYS  = 7   — based on RBI/NPCI eMandate & NACH guidelines
                               which typically allow presentment retries
                               within T+7 calendar days of the original
                               debit date before the mandate lapses.

    If either limit is breached, the record is marked "recovery_abandoned".

Terminal Actions (not subject to retry limits):
    no_retry_winback_offer   — mandate irrevocably revoked by customer
    manual_review_escalation — flagged by risk/fraud systems

Outputs:
    data/decided.json          — enriched records with chosen_action,
                                 retry_attempt_number
    output/audit_log.csv       — timestamped audit trail of every decision

Usage:
    python src/decide.py
"""

import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_RETRY_ATTEMPTS = 3   # Max automated retry attempts per subscription
RETRY_WINDOW_DAYS = 7    # Calendar days from due_date within which retries
                         # are permitted (RBI/NPCI eMandate guideline: T+7)

# Deterministic reference decision timestamp matching the synthetic batch window
DEFAULT_DECISION_DATE = datetime(2026, 9, 4, 3, 34, 6)

# ---------------------------------------------------------------------------
# Intervention mapping: failure_reason_code → chosen_action
# This is the single source of truth for what action to take per reason code.
# ---------------------------------------------------------------------------
INTERVENTION_MAP = {
    "insufficient_funds":      "retry_scheduled",
    "mandate_expired":         "reauth_request",
    "mandate_revoked":         "no_retry_winback_offer",
    "bank_technical_decline":  "immediate_retry_once",
    "instrument_expired":      "update_payment_method_request",
    "risk_block":              "manual_review_escalation",
}

# Actions that are terminal — these are one-shot, not subject to retry logic
TERMINAL_ACTIONS = {"no_retry_winback_offer", "manual_review_escalation"}

# Fields that must NEVER be accessed in this script
_FORBIDDEN_PREFIXES = ("ground_truth_",)


def is_within_retry_window(due_date_str: str, decision_date: datetime) -> bool:
    """
    Check whether the current decision date is within the allowed retry window
    from the original due date.

    Args:
        due_date_str: The original payment due date as "YYYY-MM-DD".
        decision_date: The datetime at which the decision is being made.

    Returns:
        True if within window, False if the retry window has expired.
    """
    due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
    elapsed_days = (decision_date - due_date).days
    return elapsed_days <= RETRY_WINDOW_DAYS


def decide_action(record: dict, decision_date: datetime) -> dict:
    """
    Decide the recovery intervention for a single failed record.

    Decision logic (deterministic, no LLM):
    1. Look up the default action from INTERVENTION_MAP using failure_reason_code.
    2. If the action is terminal, assign it immediately (no retry counting).
    3. For retryable actions, check stopping rules:
       a. retry_attempt_number must be <= MAX_RETRY_ATTEMPTS
       b. decision_date must be within RETRY_WINDOW_DAYS of due_date
       If either is violated, override to "recovery_abandoned".
    4. Assign retry_attempt_number (1 for first pass; incremented on re-runs).

    Args:
        record: A single payment record dict from diagnosed.json.
        decision_date: The timestamp at which this decision is being made.

    Returns:
        A new dict with added fields: chosen_action, retry_attempt_number.
    """
    enriched = dict(record)  # shallow copy — never mutate input

    # Success records need no intervention
    if record["status"] != "failed":
        enriched["chosen_action"] = None
        enriched["retry_attempt_number"] = None
        return enriched

    reason_code = record["failure_reason_code"]

    # Look up default action
    default_action = INTERVENTION_MAP.get(reason_code)
    if default_action is None:
        print(
            f"  [WARN] No intervention mapped for reason '{reason_code}' "
            f"on {record['subscription_id']} — escalating to manual_review",
            file=sys.stderr,
        )
        enriched["chosen_action"] = "manual_review_escalation"
        enriched["retry_attempt_number"] = None
        return enriched

    # Terminal actions: no retry logic, no attempt counting
    if default_action in TERMINAL_ACTIONS:
        enriched["chosen_action"] = default_action
        enriched["retry_attempt_number"] = None
        return enriched

    # --- Retryable actions: enforce stopping rules ---

    # Determine current attempt number
    # (On first run, no prior attempt exists, so this is attempt 1.
    #  On subsequent re-runs, this would be read from prior decided.json.)
    previous_attempt = record.get("retry_attempt_number")
    current_attempt = (previous_attempt or 0) + 1

    # Stopping rule 1: max retry attempts exceeded
    if current_attempt > MAX_RETRY_ATTEMPTS:
        enriched["chosen_action"] = "recovery_abandoned"
        enriched["retry_attempt_number"] = current_attempt
        return enriched

    # Stopping rule 2: retry window expired
    if not is_within_retry_window(record["due_date"], decision_date):
        enriched["chosen_action"] = "recovery_abandoned"
        enriched["retry_attempt_number"] = current_attempt
        return enriched

    # All checks passed — assign the intervention
    enriched["chosen_action"] = default_action
    enriched["retry_attempt_number"] = current_attempt

    return enriched


def write_audit_log(decided_records: list, output_path: Path, decision_ts: str):
    """
    Write a CSV audit log of every decision made.

    Columns: timestamp, subscription_id, detected_issue, diagnosed_cause,
             chosen_action, retry_attempt_number
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp",
            "subscription_id",
            "detected_issue",
            "diagnosed_cause",
            "chosen_action",
            "retry_attempt_number",
        ])

        for r in decided_records:
            if r["status"] != "failed":
                continue  # only audit failed records
            writer.writerow([
                decision_ts,
                r["subscription_id"],
                r["failure_reason_code"],
                r["diagnosed_cause"],
                r["chosen_action"],
                r["retry_attempt_number"] if r["retry_attempt_number"] is not None else "",
            ])


def main():
    """Load diagnosed.json, decide actions, write decided.json + audit_log.csv."""
    base_dir = Path(__file__).resolve().parent.parent
    input_path = base_dir / "data" / "diagnosed.json"
    output_path = base_dir / "data" / "decided.json"
    audit_path = base_dir / "output" / "audit_log.csv"

    # --- Load ---
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    # Decision timestamp (deterministic anchor for batch reproducibility; can be overridden via DECISION_DATE)
    env_date = os.environ.get("DECISION_DATE")
    if env_date:
        try:
            decision_date = datetime.fromisoformat(env_date)
        except ValueError:
            decision_date = DEFAULT_DECISION_DATE
    else:
        decision_date = DEFAULT_DECISION_DATE
    decision_ts = decision_date.strftime("%Y-%m-%d %H:%M:%S")

    # --- Decide ---
    decided = [decide_action(r, decision_date) for r in records]

    # --- Write decided.json ---
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(decided, f, indent=2)

    # --- Write audit log ---
    write_audit_log(decided, audit_path, decision_ts)

    # --- Summary ---
    failed = [r for r in decided if r["status"] == "failed"]
    action_counts = {}
    for r in failed:
        action = r["chosen_action"]
        action_counts[action] = action_counts.get(action, 0) + 1

    print("=" * 60)
    print(" AI REVENUE RECOVERY AGENT — DECISION SUMMARY")
    print("=" * 60)
    print(f"Total Records Processed : {len(decided)}")
    print(f"Failed Records Decided  : {len(failed)}")
    print(f"Decision Timestamp      : {decision_ts}")
    print(f"Output (decisions)      : {output_path.resolve()}")
    print(f"Output (audit log)      : {audit_path.resolve()}")
    print("-" * 60)
    print(f"Stopping Rules:")
    print(f"  Max retry attempts    : {MAX_RETRY_ATTEMPTS}")
    print(f"  Retry window (days)   : {RETRY_WINDOW_DAYS} (RBI/NPCI eMandate T+7)")
    print("-" * 60)
    print("Chosen Action Distribution:")
    print(f"  {'Action':<34} {'Count':<8} {'% of Failed'}")
    print("  " + "-" * 54)
    for action, count in sorted(action_counts.items(), key=lambda x: -x[1]):
        pct = count / len(failed) * 100 if failed else 0.0
        print(f"  {action:<34} {count:<8} {pct:>5.1f}%")
    print("=" * 60)
    print("Decision engine complete.")


if __name__ == "__main__":
    main()
