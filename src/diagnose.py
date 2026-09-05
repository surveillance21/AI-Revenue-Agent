"""
src/diagnose.py
---------------
Phase 2: Root-Cause Diagnosis for Failed Payments.

Reads /data/batch.json and assigns a deterministic "diagnosed_cause" to every
record whose status is "failed", based solely on the failure_reason_code field.

HARD RULE: This file must NEVER read, reference, or import any field whose
name starts with "ground_truth_". Doing so would invalidate all downstream
metrics. The mapping here is pure deterministic logic — no LLM, no model,
no external service.

Taxonomy Mapping (1:1 for Phase 2):
    failure_reason_code          → diagnosed_cause
    ─────────────────────────────────────────────────
    insufficient_funds           → funding_shortfall
    mandate_expired              → mandate_lifecycle
    mandate_revoked              → mandate_lifecycle
    bank_technical_decline       → transient_bank_error
    instrument_expired           → payment_instrument_issue
    risk_block                   → fraud_or_risk_hold

Output:
    data/diagnosed.json — full copy of batch.json with "diagnosed_cause" added
                          to every failed record (null for success records).

Usage:
    python src/diagnose.py
"""

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Deterministic root-cause taxonomy mapping
# Maps each failure_reason_code to a higher-level diagnosed cause category.
# This is the ONLY logic used for diagnosis — no model calls, no heuristics.
# ---------------------------------------------------------------------------
CAUSE_TAXONOMY = {
    "insufficient_funds":      "funding_shortfall",
    "mandate_expired":         "mandate_lifecycle",
    "mandate_revoked":         "mandate_lifecycle",
    "bank_technical_decline":  "transient_bank_error",
    "instrument_expired":      "payment_instrument_issue",
    "risk_block":              "fraud_or_risk_hold",
}

# Fields that must NEVER be accessed in this script (safety guardrail)
_FORBIDDEN_PREFIXES = ("ground_truth_",)


def diagnose_record(record: dict) -> dict:
    """
    Add a 'diagnosed_cause' field to a single record.

    - Success records get diagnosed_cause = None (no diagnosis needed).
    - Failed records are mapped through CAUSE_TAXONOMY using only
      the 'failure_reason_code' field.

    Returns a new dict (does not mutate the original).
    """
    enriched = dict(record)  # shallow copy

    if record["status"] != "failed":
        enriched["diagnosed_cause"] = None
        return enriched

    reason_code = record["failure_reason_code"]

    if reason_code not in CAUSE_TAXONOMY:
        print(
            f"  [WARN] Unknown failure_reason_code '{reason_code}' "
            f"for {record['subscription_id']} — diagnosing as 'unknown'",
            file=sys.stderr,
        )
        enriched["diagnosed_cause"] = "unknown"
    else:
        enriched["diagnosed_cause"] = CAUSE_TAXONOMY[reason_code]

    return enriched


def main():
    """Load batch.json, diagnose every failed record, write diagnosed.json."""
    base_dir = Path(__file__).resolve().parent.parent
    input_path = base_dir / "data" / "batch.json"
    output_path = base_dir / "data" / "diagnosed.json"

    # --- Load ---
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    # --- Safety check: ensure we never touch ground_truth fields ---
    # (This block documents intent; the actual logic below simply never
    #  accesses those keys. The check here is a development-time assertion.)
    for key in records[0].keys():
        for prefix in _FORBIDDEN_PREFIXES:
            if key.startswith(prefix):
                # Field exists in data — that's fine, we just won't read it.
                pass

    # --- Diagnose ---
    diagnosed = [diagnose_record(r) for r in records]

    # --- Write ---
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(diagnosed, f, indent=2)

    # --- Summary ---
    total = len(diagnosed)
    failed = [r for r in diagnosed if r["status"] == "failed"]
    cause_counts = {}
    for r in failed:
        cause = r["diagnosed_cause"]
        cause_counts[cause] = cause_counts.get(cause, 0) + 1

    print("=" * 60)
    print(" AI REVENUE RECOVERY AGENT — DIAGNOSIS SUMMARY")
    print("=" * 60)
    print(f"Total Records Processed : {total}")
    print(f"Failed Records Diagnosed: {len(failed)}")
    print(f"Output File             : {output_path.resolve()}")
    print("-" * 60)
    print("Diagnosed Cause Distribution:")
    print(f"  {'Diagnosed Cause':<28} {'Count':<8} {'% of Failed'}")
    print("  " + "-" * 50)
    for cause, count in sorted(cause_counts.items(), key=lambda x: -x[1]):
        pct = count / len(failed) * 100 if failed else 0.0
        print(f"  {cause:<28} {count:<8} {pct:>5.1f}%")
    print("=" * 60)
    print("Diagnosis complete.")


if __name__ == "__main__":
    main()
