"""
src/generate_data.py
--------------------
Phase 1: Synthetic Data Generation for AI Revenue Recovery Agent.

Generates a batch of 150 subscription/mandate payment records with realistic
distributions of success vs failed payments, failure reason codes, and ground
truth recovery potentials.

Output:
    data/batch.json

Hard Rules & Guardrails:
    - Only standard library modules (random, json, datetime, os, pathlib) are used.
    - ground_truth_* fields represent latent reality for scoring/evaluation only.
"""

import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

# Set random seed for reproducibility
RANDOM_SEED = 42
random.seed(RANDOM_SEED)

# Constants for Generation
TOTAL_RECORDS = 700
SUCCESS_PROBABILITY = 0.90  # 90% success, 10% failed

# Failure reason codes and their specified relative distribution weights
# Weights sum to 100: 45 + 20 + 15 + 10 + 7 + 3 = 100
FAILURE_REASONS = [
    "insufficient_funds",
    "mandate_expired",
    "bank_technical_decline",
    "instrument_expired",
    "mandate_revoked",
    "risk_block",
]
FAILURE_WEIGHTS = [45, 20, 15, 10, 7, 3]

# Ground truth recovery probability by failure reason
# mandate_revoked and risk_block are non-negotiably 0.0 (non-recoverable)
RECOVERABILITY_PROBABILITIES = {
    "insufficient_funds": 0.80,       # Recoverable on subsequent retry (e.g. salary credited)
    "mandate_expired": 0.50,          # Recoverable if mandate update/re-auth link is sent
    "bank_technical_decline": 0.90,   # Recoverable on short-term technical retry
    "instrument_expired": 0.40,       # Recoverable if customer updates payment method
    "mandate_revoked": 0.0,           # Hard stop: customer revoked authorization
    "risk_block": 0.0,                # Hard stop: fraud/risk policy block
}

# Ground truth optimal recovery window (in days) when recoverable
RECOVERY_WINDOW_RANGES = {
    "insufficient_funds": (1, 5),      # Typically 1-5 days (salary / funds arrival)
    "mandate_expired": (2, 7),         # Customer needs time to authorize new mandate
    "bank_technical_decline": (1, 2),  # Short retry window (minutes/hours/next day)
    "instrument_expired": (2, 7),      # Customer needs time to enter new card/VPA
}

# Sample pool for realistic customer names
FIRST_NAMES = [
    "Aarav", "Aditi", "Rohan", "Pooja", "Vikram", "Sneha", "Ananya", "Rahul",
    "Priya", "Karan", "Divya", "Arjun", "Neha", "Amit", "Deepika", "Siddharth",
    "Meera", "Varun", "Tanvi", "Gaurav", "Isha", "Nikhil", "Rhea", "Manish"
]
LAST_NAMES = [
    "Sharma", "Patel", "Verma", "Gupta", "Mehta", "Iyer", "Nair", "Reddy",
    "Singh", "Chopra", "Deshmukh", "Joshi", "Bose", "Menon", "Kapoor", "Agarwal"
]

# Common subscription tier amounts in INR
SUBSCRIPTION_AMOUNTS = [199, 299, 499, 799, 999, 1499, 1999, 2499, 4999]


def generate_customer_name() -> str:
    """Generate a realistic full customer name."""
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def generate_due_date(base_date: datetime) -> str:
    """Generate a due date within a +/- 15 day window around base date."""
    offset = random.randint(-15, 5)
    return (base_date + timedelta(days=offset)).strftime("%Y-%m-%d")


def generate_record(idx: int, base_date: datetime) -> dict:
    """Generate a single subscription payment record."""
    subscription_id = f"sub_{100000 + idx}"
    customer_id = f"cust_{200000 + idx}"
    customer_name = generate_customer_name()
    amount_inr = random.choice(SUBSCRIPTION_AMOUNTS)
    due_date = generate_due_date(base_date)

    # Determine success vs failure based on 90/10 split
    is_success = random.random() < SUCCESS_PROBABILITY
    status = "success" if is_success else "failed"

    if status == "success":
        failure_reason_code = None
        ground_truth_recoverable = None
        ground_truth_recovery_window_days = None
    else:
        # Sample failure reason based on defined distribution
        failure_reason_code = random.choices(
            FAILURE_REASONS,
            weights=FAILURE_WEIGHTS,
            k=1
        )[0]

        # Determine latent recoverability
        recov_prob = RECOVERABILITY_PROBABILITIES[failure_reason_code]
        ground_truth_recoverable = random.random() < recov_prob

        # If recoverable, assign expected recovery window in days
        if ground_truth_recoverable:
            window_min, window_max = RECOVERY_WINDOW_RANGES[failure_reason_code]
            ground_truth_recovery_window_days = random.randint(window_min, window_max)
        else:
            ground_truth_recovery_window_days = None

    return {
        "subscription_id": subscription_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "amount_inr": amount_inr,
        "due_date": due_date,
        "status": status,
        "failure_reason_code": failure_reason_code,
        "ground_truth_recoverable": ground_truth_recoverable,
        "ground_truth_recovery_window_days": ground_truth_recovery_window_days,
    }


def main():
    """Main execution function to generate and save batch data."""
    # Ensure data directory exists
    base_dir = Path(__file__).resolve().parent.parent
    data_dir = base_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "batch.json"

    base_date = datetime(2026, 9, 1)
    records = [generate_record(i + 1, base_date) for i in range(TOTAL_RECORDS)]

    # Write out JSON
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    # Compute and display distribution statistics
    success_count = sum(1 for r in records if r["status"] == "success")
    failed_count = sum(1 for r in records if r["status"] == "failed")
    failed_records = [r for r in records if r["status"] == "failed"]

    reason_counts = {}
    recoverable_by_reason = {}

    for r in failed_records:
        reason = r["failure_reason_code"]
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if r["ground_truth_recoverable"]:
            recoverable_by_reason[reason] = recoverable_by_reason.get(reason, 0) + 1

    print("=" * 60)
    print(" AI REVENUE RECOVERY AGENT — SYNTHETIC DATA GENERATION SUMMARY")
    print("=" * 60)
    print(f"Total Records Generated : {len(records)}")
    print(f"Output File             : {output_path.resolve()}")
    print("-" * 60)
    print(f"Status Distribution:")
    print(f"  - Success : {success_count} ({success_count / len(records) * 100:.1f}%)")
    print(f"  - Failed  : {failed_count} ({failed_count / len(records) * 100:.1f}%)")
    print("-" * 60)
    print("Failure Reasons Breakdown & Recoverability:")
    print(f"  {'Reason Code':<26} {'Count':<8} {'% of Failed':<14} {'Recoverable':<12}")
    print("  " + "-" * 58)

    for reason in FAILURE_REASONS:
        count = reason_counts.get(reason, 0)
        pct = (count / failed_count * 100) if failed_count > 0 else 0.0
        rec_count = recoverable_by_reason.get(reason, 0)
        print(f"  {reason:<26} {count:<8} {pct:>5.1f}%        {rec_count}/{count} ({ (rec_count/count*100) if count > 0 else 0.0:.0f}%)")

    print("=" * 60)
    print("Data generation complete.")


if __name__ == "__main__":
    main()
