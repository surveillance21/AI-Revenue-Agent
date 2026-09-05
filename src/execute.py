"""
src/execute.py
--------------
Phase 4: Simulated Execution & Outcome Resolution.

Reads /data/decided.json and simulates the execution of each chosen recovery
action. For each failed record:
    1. Logs the action as "executed" (no real API calls).
    2. Reads ground_truth_recoverable and ground_truth_recovery_window_days
       (ONLY permitted in this file and report.py) to determine a simulated
       outcome.

Outcome categories:
    "recovered"         — A recovery action was attempted AND ground truth
                          confirms the payment was recoverable AND the
                          simulated execution fell within the recovery window.
    "still_failed"      — A recovery action was attempted but ground truth
                          says it was unrecoverable, OR the execution happened
                          outside the recovery window, OR a stop/abandon
                          decision was made on a record that was actually
                          recoverable (missed opportunity).
    "correctly_stopped" — A terminal action (no_retry_winback_offer,
                          manual_review_escalation) or recovery_abandoned was
                          chosen AND ground truth confirms the payment was
                          indeed unrecoverable. The agent made the right call.

Simulated execution delays by action type (days from due_date):
    immediate_retry_once           → 0 days  (same-day retry)
    retry_scheduled                → 3 days  (near next salary credit cycle)
    reauth_request                 → 2 days  (customer response time)
    update_payment_method_request  → 3 days  (customer updates instrument)

Outputs:
    data/final_results.json     — full records with "execution_outcome" added
    output/audit_log.csv        — updated with new "outcome" column

Usage:
    python src/execute.py
"""

import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Simulated execution delay (days after due_date) per action type.
# These represent realistic turnaround times for each intervention.
# ---------------------------------------------------------------------------
EXECUTION_DELAY_DAYS = {
    "immediate_retry_once":           0,   # Retried on the same day
    "retry_scheduled":                3,   # Scheduled near next salary cycle
    "reauth_request":                 2,   # Customer responds to re-auth link
    "update_payment_method_request":  3,   # Customer updates card/VPA
}

# Actions that are terminal / non-retry — these don't have execution delays
TERMINAL_OR_ABANDONED_ACTIONS = {
    "no_retry_winback_offer",
    "manual_review_escalation",
    "recovery_abandoned",
}


def simulate_outcome(record: dict) -> str:
    """
    Determine the simulated outcome of an executed recovery action.

    This function is the ONLY place (along with report.py) that is permitted
    to read ground_truth_* fields.

    Logic:
        For terminal / abandoned actions:
            - ground_truth_recoverable is False → "correctly_stopped"
            - ground_truth_recoverable is True  → "still_failed" (missed opp.)

        For retryable actions:
            - ground_truth_recoverable is False → "still_failed"
            - ground_truth_recoverable is True:
                - Compute simulated execution day = due_date + action delay
                - If delay_days <= ground_truth_recovery_window_days → "recovered"
                - Else → "still_failed" (retried too late)

    Args:
        record: A single payment record dict from decided.json.

    Returns:
        One of "recovered", "still_failed", "correctly_stopped".
    """
    action = record["chosen_action"]
    gt_recoverable = record["ground_truth_recoverable"]
    gt_window = record["ground_truth_recovery_window_days"]

    # --- Terminal or abandoned actions ---
    if action in TERMINAL_OR_ABANDONED_ACTIONS:
        if gt_recoverable is False:
            return "correctly_stopped"
        else:
            # We stopped, but the payment was actually recoverable — a miss.
            return "still_failed"

    # --- Retryable actions ---
    if gt_recoverable is False:
        return "still_failed"

    # Recoverable: check if our execution timing is within the window
    delay = EXECUTION_DELAY_DAYS.get(action, 0)
    if gt_window is not None and delay <= gt_window:
        return "recovered"
    else:
        # Retried outside the recovery window — too late
        return "still_failed"


def execute_record(record: dict) -> dict:
    """
    Simulate execution of the decided action for a single record.

    - Success records pass through unchanged (no execution needed).
    - Failed records get "execution_outcome" based on ground truth simulation.

    Args:
        record: A single record from decided.json.

    Returns:
        A new dict with the added field "execution_outcome".
    """
    enriched = dict(record)  # shallow copy

    if record["status"] != "failed":
        enriched["execution_outcome"] = None
        return enriched

    action = record["chosen_action"]

    # --- Log execution (simulated, no real API calls) ---
    if action in TERMINAL_OR_ABANDONED_ACTIONS:
        print(
            f"  [EXEC] {record['subscription_id']}  "
            f"Action: {action:<34}  -> No retry executed (terminal/abandoned)"
        )
    else:
        delay = EXECUTION_DELAY_DAYS.get(action, 0)
        print(
            f"  [EXEC] {record['subscription_id']}  "
            f"Action: {action:<34}  -> Simulated execution (T+{delay}d)"
        )

    # --- Determine outcome using ground truth ---
    outcome = simulate_outcome(record)
    enriched["execution_outcome"] = outcome

    print(
        f"         \\-- Outcome: {outcome}"
    )

    return enriched


def update_audit_log(audit_path: Path, final_records: list):
    """
    Read the existing audit_log.csv, append the "outcome" column by matching
    on subscription_id, and write it back.

    Args:
        audit_path: Path to output/audit_log.csv.
        final_records: The list of fully-enriched records from execution.
    """
    # Build a lookup: subscription_id → execution_outcome
    outcome_lookup = {}
    for r in final_records:
        if r["status"] == "failed":
            outcome_lookup[r["subscription_id"]] = r["execution_outcome"]

    # Read existing audit log
    rows = []
    with open(audit_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing_fieldnames = list(reader.fieldnames)
        for row in reader:
            rows.append(row)

    # Add outcome column
    updated_fieldnames = existing_fieldnames + ["outcome"]

    for row in rows:
        sid = row["subscription_id"]
        row["outcome"] = outcome_lookup.get(sid, "")

    # Write back
    with open(audit_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=updated_fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    """Load decided.json, simulate execution, write final_results.json + update audit log."""
    base_dir = Path(__file__).resolve().parent.parent
    input_path = base_dir / "data" / "decided.json"
    output_path = base_dir / "data" / "final_results.json"
    audit_path = base_dir / "output" / "audit_log.csv"

    # --- Load ---
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    # --- Execute ---
    print("=" * 70)
    print(" AI REVENUE RECOVERY AGENT — EXECUTION LOG")
    print("=" * 70)

    final = [execute_record(r) for r in records]

    # --- Write final_results.json ---
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final, f, indent=2)

    # --- Update audit log with outcomes ---
    if audit_path.exists():
        update_audit_log(audit_path, final)
        print(f"\n  Audit log updated: {audit_path.resolve()}")
    else:
        print(
            f"\n  [WARN] Audit log not found at {audit_path}; skipping update.",
            file=sys.stderr,
        )

    # --- Summary ---
    failed = [r for r in final if r["status"] == "failed"]
    outcome_counts = {}
    for r in failed:
        outcome = r["execution_outcome"]
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

    recovered_amount = sum(
        r["amount_inr"] for r in failed if r["execution_outcome"] == "recovered"
    )
    total_failed_amount = sum(r["amount_inr"] for r in failed)

    print("\n" + "=" * 70)
    print(" EXECUTION SUMMARY")
    print("=" * 70)
    print(f"Total Records Processed  : {len(final)}")
    print(f"Failed Records Executed  : {len(failed)}")
    print(f"Output (final results)   : {output_path.resolve()}")
    print("-" * 70)
    print("Outcome Distribution:")
    print(f"  {'Outcome':<24} {'Count':<8} {'% of Failed'}")
    print("  " + "-" * 44)
    for outcome, count in sorted(outcome_counts.items(), key=lambda x: -x[1]):
        pct = count / len(failed) * 100 if failed else 0.0
        print(f"  {outcome:<24} {count:<8} {pct:>5.1f}%")
    print("-" * 70)
    print(f"Revenue at Risk (failed) : INR {total_failed_amount:,}")
    print(f"Revenue Recovered        : INR {recovered_amount:,}")
    if total_failed_amount > 0:
        print(
            f"Recovery Rate (amount)   : "
            f"{recovered_amount / total_failed_amount * 100:.1f}%"
        )
    print("=" * 70)
    print("Execution complete.")


if __name__ == "__main__":
    main()
