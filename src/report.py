"""
src/report.py
-------------
Phase 5: Recovery Metrics Report & Visualization.

Reads /data/final_results.json and /output/audit_log.csv to compute and display
comprehensive recovery metrics, then generates a bar chart saved to
/output/metrics_chart.png.

This file IS permitted to read ground_truth_* fields for scoring purposes.

Metrics computed:
    1. Total records processed, total failed, total revenue at risk (INR)
    2. Revenue recovered (INR) and recovery rate (%)
    3. Diagnosis quality assessment:
       - Flags any records where "correctly_stopped" outcome co-occurs with
         ground_truth_recoverable=True (diagnosis error / missed opportunity)
    4. False-positive cost:
       - Cases where a retry action was attempted but ground_truth_recoverable
         was False (wasted effort)
       - Estimated cost at INR 15 per retry attempt (API call + processing)
    5. Correctly-stopped count (mandate_revoked / risk_block not retried and
       confirmed unrecoverable by ground truth)

Output:
    - Console summary table
    - /output/metrics_chart.png  (bar chart: at-risk vs recovered revenue)

Usage:
    python src/report.py
"""

import csv
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for headless chart generation
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# ---------------------------------------------------------------------------
# Cost assumption for false-positive retries
# ---------------------------------------------------------------------------
COST_PER_WASTED_RETRY_INR = 15  # Estimated cost per failed retry attempt
                                 # (API call, SMS/email notification, processing)


def load_data(base_dir: Path) -> tuple:
    """
    Load final_results.json and audit_log.csv.

    Returns:
        (final_records, audit_rows) tuple.
    """
    results_path = base_dir / "data" / "final_results.json"
    audit_path = base_dir / "output" / "audit_log.csv"

    if not results_path.exists():
        print(f"ERROR: {results_path} not found.", file=sys.stderr)
        sys.exit(1)
    if not audit_path.exists():
        print(f"ERROR: {audit_path} not found.", file=sys.stderr)
        sys.exit(1)

    with open(results_path, "r", encoding="utf-8") as f:
        final_records = json.load(f)

    with open(audit_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        audit_rows = list(reader)

    return final_records, audit_rows


def compute_metrics(failed_records: list) -> dict:
    """
    Compute all recovery metrics from the failed records.

    This function reads ground_truth_* fields (permitted in report.py).

    Args:
        failed_records: List of record dicts with status="failed".

    Returns:
        Dict of computed metric values.
    """
    total_failed = len(failed_records)
    total_at_risk = sum(r["amount_inr"] for r in failed_records)

    # --- Outcome counts ---
    recovered_records = [r for r in failed_records if r["execution_outcome"] == "recovered"]
    still_failed_records = [r for r in failed_records if r["execution_outcome"] == "still_failed"]
    correctly_stopped_records = [r for r in failed_records if r["execution_outcome"] == "correctly_stopped"]

    recovered_count = len(recovered_records)
    still_failed_count = len(still_failed_records)
    correctly_stopped_count = len(correctly_stopped_records)

    recovered_amount = sum(r["amount_inr"] for r in recovered_records)
    recovery_rate_pct = (recovered_amount / total_at_risk * 100) if total_at_risk > 0 else 0.0

    # --- Diagnosis errors ---
    # A diagnosis error is when we marked something as "correctly_stopped"
    # but ground truth says it was actually recoverable (missed opportunity).
    # Note: by our execute.py logic, correctly_stopped requires
    # ground_truth_recoverable=False, so this should always be 0 unless
    # there's a bug. We check anyway for auditability.
    diagnosis_errors = [
        r for r in correctly_stopped_records
        if r["ground_truth_recoverable"] is True
    ]

    # --- False positives (wasted retries) ---
    # Cases where a non-terminal retry action was attempted but the payment
    # was actually unrecoverable per ground truth.
    terminal_actions = {"no_retry_winback_offer", "manual_review_escalation", "recovery_abandoned"}
    retried_records = [r for r in failed_records if r["chosen_action"] not in terminal_actions]
    false_positive_retries = [
        r for r in retried_records
        if r["ground_truth_recoverable"] is False
    ]
    false_positive_count = len(false_positive_retries)
    false_positive_cost = false_positive_count * COST_PER_WASTED_RETRY_INR

    # --- Missed recoveries ---
    # Records that were recoverable but we either abandoned or terminally stopped them
    missed_recoveries = [
        r for r in failed_records
        if r["ground_truth_recoverable"] is True and r["execution_outcome"] != "recovered"
    ]
    missed_recovery_amount = sum(r["amount_inr"] for r in missed_recoveries)

    # --- Correctly stopped breakdown by reason ---
    correctly_stopped_by_reason = {}
    for r in correctly_stopped_records:
        reason = r["failure_reason_code"]
        correctly_stopped_by_reason[reason] = correctly_stopped_by_reason.get(reason, 0) + 1

    # --- Outcome by failure reason (for detailed breakdown) ---
    reason_outcome_matrix = {}
    for r in failed_records:
        reason = r["failure_reason_code"]
        outcome = r["execution_outcome"]
        if reason not in reason_outcome_matrix:
            reason_outcome_matrix[reason] = {}
        reason_outcome_matrix[reason][outcome] = reason_outcome_matrix[reason].get(outcome, 0) + 1

    return {
        "total_failed": total_failed,
        "total_at_risk": total_at_risk,
        "recovered_count": recovered_count,
        "recovered_amount": recovered_amount,
        "recovery_rate_pct": recovery_rate_pct,
        "still_failed_count": still_failed_count,
        "correctly_stopped_count": correctly_stopped_count,
        "correctly_stopped_by_reason": correctly_stopped_by_reason,
        "diagnosis_error_count": len(diagnosis_errors),
        "diagnosis_errors": diagnosis_errors,
        "false_positive_count": false_positive_count,
        "false_positive_cost": false_positive_cost,
        "false_positive_retries": false_positive_retries,
        "missed_recovery_count": len(missed_recoveries),
        "missed_recovery_amount": missed_recovery_amount,
        "reason_outcome_matrix": reason_outcome_matrix,
    }


def print_report(metrics: dict, total_records: int):
    """Print a formatted summary report to stdout."""

    print("=" * 70)
    print(" AI REVENUE RECOVERY AGENT -- RECOVERY METRICS REPORT")
    print("=" * 70)

    # --- Section 1: Overview ---
    print("\n  1. OVERVIEW")
    print("  " + "-" * 66)
    print(f"  Total records processed       : {total_records}")
    print(f"  Total failed                  : {metrics['total_failed']}")
    print(f"  Total revenue at risk         : INR {metrics['total_at_risk']:,}")

    # --- Section 2: Recovery Performance ---
    print(f"\n  2. RECOVERY PERFORMANCE")
    print("  " + "-" * 66)
    print(f"  Revenue recovered             : INR {metrics['recovered_amount']:,}")
    print(f"  Recovery rate (by amount)     : {metrics['recovery_rate_pct']:.1f}%")
    print(f"  Payments recovered            : {metrics['recovered_count']} / {metrics['total_failed']}")
    print(f"  Still failed                  : {metrics['still_failed_count']}")
    print(f"  Correctly stopped             : {metrics['correctly_stopped_count']}")

    # --- Section 3: Diagnosis Quality ---
    print(f"\n  3. DIAGNOSIS QUALITY ASSESSMENT")
    print("  " + "-" * 66)
    if metrics["diagnosis_error_count"] == 0:
        print("  Diagnosis errors              : 0  (no misclassifications detected)")
    else:
        print(f"  Diagnosis errors              : {metrics['diagnosis_error_count']}  [!!! FLAG]")
        print("  The following records were marked 'correctly_stopped' but")
        print("  ground_truth_recoverable was True (missed opportunities):")
        for r in metrics["diagnosis_errors"]:
            print(f"    - {r['subscription_id']}  reason={r['failure_reason_code']}  "
                  f"amount=INR {r['amount_inr']:,}")

    # --- Section 4: False-Positive Cost ---
    print(f"\n  4. FALSE-POSITIVE COST (WASTED RETRIES)")
    print("  " + "-" * 66)
    print(f"  Retry attempts on unrecoverable : {metrics['false_positive_count']}")
    print(f"  Cost per wasted retry           : INR {COST_PER_WASTED_RETRY_INR}")
    print(f"  Total wasted retry cost         : INR {metrics['false_positive_cost']:,}")
    if metrics["false_positive_count"] > 0:
        print("  Records with wasted retries:")
        for r in metrics["false_positive_retries"]:
            print(f"    - {r['subscription_id']}  action={r['chosen_action']}  "
                  f"reason={r['failure_reason_code']}")

    # --- Section 5: Correctly Stopped Breakdown ---
    print(f"\n  5. CORRECTLY STOPPED BREAKDOWN")
    print("  " + "-" * 66)
    print(f"  Total correctly stopped         : {metrics['correctly_stopped_count']}")
    if metrics["correctly_stopped_by_reason"]:
        print(f"  {'Failure Reason':<28} {'Count'}")
        print("  " + "-" * 36)
        for reason, count in sorted(metrics["correctly_stopped_by_reason"].items(),
                                    key=lambda x: -x[1]):
            print(f"  {reason:<28} {count}")
    else:
        print("  (none)")

    # --- Section 6: Missed Recoveries ---
    print(f"\n  6. MISSED RECOVERY OPPORTUNITIES")
    print("  " + "-" * 66)
    print(f"  Missed recoverable payments     : {metrics['missed_recovery_count']}")
    print(f"  Missed recoverable revenue      : INR {metrics['missed_recovery_amount']:,}")

    # --- Section 7: Outcome by Failure Reason ---
    print(f"\n  7. OUTCOME BY FAILURE REASON")
    print("  " + "-" * 66)
    outcomes = ["recovered", "still_failed", "correctly_stopped"]
    header = f"  {'Failure Reason':<26}"
    for o in outcomes:
        header += f" {o:<18}"
    print(header)
    print("  " + "-" * 80)
    for reason, outcome_map in sorted(metrics["reason_outcome_matrix"].items()):
        row = f"  {reason:<26}"
        for o in outcomes:
            count = outcome_map.get(o, 0)
            row += f" {count:<18}"
        print(row)

    # --- Net value ---
    net_value = metrics["recovered_amount"] - metrics["false_positive_cost"]
    print(f"\n  " + "=" * 66)
    print(f"  NET RECOVERY VALUE")
    print(f"  Revenue recovered               : INR {metrics['recovered_amount']:,}")
    print(f"  Less: wasted retry cost         : INR {metrics['false_positive_cost']:,}")
    print(f"  Net value                       : INR {net_value:,}")
    print("  " + "=" * 66)


def generate_chart(metrics: dict, output_path: Path):
    """
    Generate a bar chart comparing at-risk vs recovered revenue.
    Saved as a PNG to the specified output path.
    """
    at_risk = metrics["total_at_risk"]
    recovered = metrics["recovered_amount"]
    missed = metrics["missed_recovery_amount"]
    still_failed_unrecoverable = at_risk - recovered - missed

    categories = [
        "Revenue\nAt Risk",
        "Revenue\nRecovered",
        "Missed\nRecoveries",
        "Unrecoverable\n(Correctly Handled)",
    ]
    values = [at_risk, recovered, missed, still_failed_unrecoverable]
    colors = ["#E74C3C", "#27AE60", "#F39C12", "#95A5A6"]

    fig, ax = plt.subplots(figsize=(10, 6))

    bars = ax.bar(categories, values, color=colors, width=0.6, edgecolor="white",
                  linewidth=1.5)

    # Add value labels on each bar
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + max(values) * 0.02,
            f"INR {val:,}",
            ha="center", va="bottom",
            fontsize=11, fontweight="bold", color="#2C3E50",
        )

    ax.set_title(
        "AI Revenue Recovery Agent -- Recovery Metrics",
        fontsize=16, fontweight="bold", color="#2C3E50", pad=20,
    )
    ax.set_ylabel("Amount (INR)", fontsize=12, color="#2C3E50")
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.set_ylim(0, max(values) * 1.20)

    # Style
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#BDC3C7")
    ax.spines["bottom"].set_color("#BDC3C7")
    ax.tick_params(colors="#7F8C8D", labelsize=10)
    ax.set_facecolor("#FAFAFA")
    fig.patch.set_facecolor("#FFFFFF")

    # Recovery rate annotation
    rate = metrics["recovery_rate_pct"]
    ax.annotate(
        f"Recovery Rate: {rate:.1f}%",
        xy=(0.98, 0.95), xycoords="axes fraction",
        fontsize=13, fontweight="bold", color="#27AE60",
        ha="right", va="top",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="#E8F8F5", edgecolor="#27AE60",
                  alpha=0.9),
    )

    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\n  Chart saved to: {output_path.resolve()}")


def main():
    """Main entry point: load data, compute metrics, print report, generate chart."""
    base_dir = Path(__file__).resolve().parent.parent
    chart_path = base_dir / "output" / "metrics_chart.png"

    final_records, audit_rows = load_data(base_dir)

    total_records = len(final_records)
    failed_records = [r for r in final_records if r["status"] == "failed"]

    if not failed_records:
        print("No failed records found. Nothing to report.")
        sys.exit(0)

    metrics = compute_metrics(failed_records)
    print_report(metrics, total_records)
    generate_chart(metrics, chart_path)

    # --- Save metrics summary as JSON for downstream consumption ---
    metrics_json_path = base_dir / "output" / "metrics_summary.json"
    serializable_metrics = {
        "total_records": total_records,
        "total_failed": metrics["total_failed"],
        "total_at_risk_inr": metrics["total_at_risk"],
        "recovered_count": metrics["recovered_count"],
        "recovered_amount_inr": metrics["recovered_amount"],
        "recovery_rate_pct": round(metrics["recovery_rate_pct"], 2),
        "still_failed_count": metrics["still_failed_count"],
        "correctly_stopped_count": metrics["correctly_stopped_count"],
        "diagnosis_error_count": metrics["diagnosis_error_count"],
        "false_positive_retries": metrics["false_positive_count"],
        "false_positive_cost_inr": metrics["false_positive_cost"],
        "missed_recovery_count": metrics["missed_recovery_count"],
        "missed_recovery_amount_inr": metrics["missed_recovery_amount"],
        "net_recovery_value_inr": metrics["recovered_amount"] - metrics["false_positive_cost"],
    }
    with open(metrics_json_path, "w", encoding="utf-8") as f:
        json.dump(serializable_metrics, f, indent=2)
    print(f"  Metrics JSON saved to: {metrics_json_path.resolve()}")

    # --- Generate freshly updated dashboard.html ---
    dashboard_html_path = base_dir / "output" / "dashboard.html"
    generate_dashboard_html(serializable_metrics, metrics, audit_rows, dashboard_html_path)
    print(f"  Dashboard HTML updated: {dashboard_html_path.resolve()}")

    print("\nReport complete.")


def generate_dashboard_html(summary_metrics: dict, metrics: dict, audit_rows: list, output_path: Path):
    """
    Generate or update output/dashboard.html with the latest metrics and audit log.
    Embeds the fresh dataset so the page works 100% offline via double-click (file://),
    while also supporting live fetch() if served over HTTP.
    """
    # Build reason breakdown data
    reason_breakdown = {}
    cause_map = {
        "insufficient_funds": "funding_shortfall",
        "mandate_expired": "mandate_lifecycle",
        "mandate_revoked": "mandate_lifecycle",
        "bank_technical_decline": "transient_bank_error",
        "instrument_expired": "payment_instrument_issue",
        "risk_block": "fraud_or_risk_hold",
    }
    
    for r in audit_rows:
        reason = r.get("detected_issue")
        outcome = r.get("outcome")
        if not reason:
            continue
        if reason not in reason_breakdown:
            reason_breakdown[reason] = {
                "recovered": 0, "still_failed": 0, "correctly_stopped": 0,
                "total": 0, "amount_at_risk": 0, "amount_recovered": 0
            }
        reason_breakdown[reason][outcome] = reason_breakdown[reason].get(outcome, 0) + 1
        reason_breakdown[reason]["total"] += 1

    # Add amounts from final_results if available
    final_results_path = output_path.parent.parent / "data" / "final_results.json"
    if final_results_path.exists():
        with open(final_results_path, "r", encoding="utf-8") as f:
            final_data = json.load(f)
            for item in final_data:
                if item.get("status") == "failed":
                    rsn = item.get("failure_reason_code")
                    if rsn in reason_breakdown:
                        reason_breakdown[rsn]["amount_at_risk"] += item.get("amount_inr", 0)
                        if item.get("execution_outcome") == "recovered":
                            reason_breakdown[rsn]["amount_recovered"] += item.get("amount_inr", 0)

    # Read dashboard template or render
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI Revenue Recovery Agent — Executive Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #0b0f19;
      --bg-surface: #111827;
      --bg-surface-elevated: #162032;
      --card-bg: rgba(17, 24, 39, 0.75);
      --card-border: rgba(255, 255, 255, 0.08);
      --card-border-hover: rgba(255, 255, 255, 0.16);
      --text-main: #f8fafc;
      --text-secondary: #94a3b8;
      --text-muted: #64748b;
      
      --primary: #3b82f6;
      --primary-glow: rgba(59, 130, 246, 0.25);
      --primary-subtle: rgba(59, 130, 246, 0.12);
      --primary-border: rgba(59, 130, 246, 0.3);
      
      --success: #10b981;
      --success-glow: rgba(16, 185, 129, 0.25);
      --success-subtle: rgba(16, 185, 129, 0.12);
      --success-border: rgba(16, 185, 129, 0.3);
      
      --danger: #f43f5e;
      --danger-glow: rgba(244, 63, 94, 0.25);
      --danger-subtle: rgba(244, 63, 94, 0.12);
      --danger-border: rgba(244, 63, 94, 0.3);
      
      --warning: #f59e0b;
      --warning-glow: rgba(245, 158, 11, 0.25);
      --warning-subtle: rgba(245, 158, 11, 0.12);
      --warning-border: rgba(245, 158, 11, 0.3);
      
      --purple: #a855f7;
      --purple-glow: rgba(168, 85, 247, 0.25);
      --purple-subtle: rgba(168, 85, 247, 0.12);
      --purple-border: rgba(168, 85, 247, 0.3);

      --radius-sm: 8px;
      --radius-md: 12px;
      --radius-lg: 16px;
      --radius-full: 9999px;
    }}

    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}

    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background-color: var(--bg);
      background-image: 
        radial-gradient(1200px 600px at 50% -80px, rgba(59, 130, 246, 0.08), transparent 70%),
        radial-gradient(900px 500px at 90% 120px, rgba(168, 85, 247, 0.05), transparent 65%),
        radial-gradient(800px 500px at 10% 400px, rgba(16, 185, 129, 0.04), transparent 65%);
      background-attachment: fixed;
      color: var(--text-main);
      padding: 36px 32px 64px;
      min-height: 100vh;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }}

    .container {{
      max-width: 1440px;
      margin: 0 auto;
    }}

    /* ==========================================================================
       Header Styling
       ========================================================================== */
    header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 36px;
      padding-bottom: 28px;
      border-bottom: 1px solid var(--card-border);
      flex-wrap: wrap;
      gap: 28px;
    }}

    .brand-section {{
      display: flex;
      align-items: flex-start;
      gap: 16px;
      flex: 1;
      min-width: 320px;
    }}

    .brand-icon-box {{
      width: 48px;
      height: 48px;
      border-radius: var(--radius-md);
      background: linear-gradient(135deg, #2563eb 0%, #7c3aed 100%);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #ffffff;
      box-shadow: 0 8px 16px -4px rgba(37, 99, 235, 0.35);
      flex-shrink: 0;
      margin-top: 2px;
    }}

    .title-group {{
      flex: 1;
    }}

    .kicker-row {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
      flex-wrap: wrap;
    }}

    .kicker {{
      display: inline-flex;
      align-items: center;
      gap: 7px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 1px;
      text-transform: uppercase;
      color: #38bdf8;
      background: rgba(56, 189, 248, 0.1);
      border: 1px solid rgba(56, 189, 248, 0.25);
      padding: 4px 12px;
      border-radius: var(--radius-full);
    }}

    .live-pulse {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.8px;
      text-transform: uppercase;
      color: #34d399;
      background: rgba(16, 185, 129, 0.1);
      border: 1px solid rgba(16, 185, 129, 0.25);
      padding: 4px 10px;
      border-radius: var(--radius-full);
    }}

    .pulse-dot {{
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: #10b981;
      box-shadow: 0 0 8px #10b981;
      animation: pulse-ring 2s infinite cubic-bezier(0.4, 0, 0.6, 1);
    }}

    @keyframes pulse-ring {{
      0% {{ box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }}
      70% {{ box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); }}
      100% {{ box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }}
    }}

    .title-group h1 {{
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -0.025em;
      color: var(--text-main);
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }}

    .title-gradient {{
      background: linear-gradient(180deg, #ffffff 0%, #cbd5e1 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}

    .title-sub {{
      font-size: 14px;
      color: var(--text-secondary);
      margin-top: 6px;
      max-width: 680px;
      line-height: 1.5;
    }}

    .header-actions {{
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 14px;
    }}

    .badges-row {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
      justify-content: flex-end;
    }}

    .badge-pill {{
      font-size: 12px;
      padding: 5px 12px;
      border-radius: var(--radius-full);
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      backdrop-filter: blur(8px);
      transition: all 0.2s ease;
    }}

    .badge-compliance {{
      background: var(--primary-subtle);
      color: #93c5fd;
      border: 1px solid var(--primary-border);
    }}

    .badge-batch {{
      background: var(--success-subtle);
      color: #6ee7b7;
      border: 1px solid var(--success-border);
    }}

    .badge-source {{
      background: var(--purple-subtle);
      color: #d8b4fe;
      border: 1px solid var(--purple-border);
    }}

    .controls-row {{
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}

    .btn-upload {{
      cursor: pointer;
      background: rgba(255, 255, 255, 0.05);
      color: #e2e8f0;
      border: 1px solid rgba(255, 255, 255, 0.12);
      padding: 7px 14px;
      border-radius: 10px;
      font-size: 12px;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: all 0.2s ease;
      backdrop-filter: blur(8px);
    }}

    .btn-upload:hover {{
      background: rgba(255, 255, 255, 0.1);
      border-color: rgba(255, 255, 255, 0.25);
      color: #ffffff;
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
    }}

    .timestamp-tag {{
      font-size: 12px;
      color: var(--text-muted);
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(255, 255, 255, 0.03);
      padding: 6px 12px;
      border-radius: 8px;
      border: 1px solid rgba(255, 255, 255, 0.05);
    }}

    .timestamp-tag strong {{
      color: var(--text-secondary);
      font-weight: 600;
    }}

    /* ==========================================================================
       Metrics Grid & Summary Cards
       ========================================================================== */
    .metrics-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 20px;
      margin-bottom: 32px;
    }}

    @media (max-width: 1100px) {{
      .metrics-grid {{
        grid-template-columns: repeat(2, 1fr);
      }}
    }}

    @media (max-width: 640px) {{
      .metrics-grid {{
        grid-template-columns: 1fr;
      }}
    }}

    .card {{
      background: var(--card-bg);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-lg);
      padding: 24px;
      box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.4), 0 2px 6px -1px rgba(0, 0, 0, 0.2);
      transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1), border-color 0.25s ease, box-shadow 0.25s ease;
      position: relative;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }}

    .card::before {{
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 3px;
      background: transparent;
      transition: opacity 0.2s ease;
    }}

    .card:hover {{
      transform: translateY(-3px);
      border-color: var(--card-border-hover);
      box-shadow: 0 16px 32px -8px rgba(0, 0, 0, 0.5), 0 4px 12px -2px rgba(0, 0, 0, 0.3);
    }}

    .card-at-risk::before {{
      background: linear-gradient(90deg, #f43f5e, #fb7185);
    }}
    .card-at-risk:hover {{
      border-color: rgba(244, 63, 94, 0.4);
      box-shadow: 0 16px 32px -8px var(--danger-glow);
    }}

    .card-recovered::before {{
      background: linear-gradient(90deg, #10b981, #34d399);
    }}
    .card-recovered:hover {{
      border-color: rgba(16, 185, 129, 0.4);
      box-shadow: 0 16px 32px -8px var(--success-glow);
    }}

    .card-rate::before {{
      background: linear-gradient(90deg, #3b82f6, #60a5fa);
    }}
    .card-rate:hover {{
      border-color: rgba(59, 130, 246, 0.4);
      box-shadow: 0 16px 32px -8px var(--primary-glow);
    }}

    .card-net::before {{
      background: linear-gradient(90deg, #a855f7, #c084fc);
    }}
    .card-net:hover {{
      border-color: rgba(168, 85, 247, 0.4);
      box-shadow: 0 16px 32px -8px var(--purple-glow);
    }}

    .card-header-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
    }}

    .card-label {{
      font-size: 11px;
      font-weight: 700;
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.8px;
    }}

    .card-icon-pill {{
      width: 36px;
      height: 36px;
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: transform 0.2s ease;
    }}

    .card:hover .card-icon-pill {{
      transform: scale(1.05);
    }}

    .card-at-risk .card-icon-pill {{ background: var(--danger-subtle); color: #fb7185; border: 1px solid var(--danger-border); }}
    .card-recovered .card-icon-pill {{ background: var(--success-subtle); color: #34d399; border: 1px solid var(--success-border); }}
    .card-rate .card-icon-pill {{ background: var(--primary-subtle); color: #60a5fa; border: 1px solid var(--primary-border); }}
    .card-net .card-icon-pill {{ background: var(--purple-subtle); color: #c084fc; border: 1px solid var(--purple-border); }}

    .card-value {{
      font-size: 34px;
      font-weight: 800;
      color: var(--text-main);
      letter-spacing: -0.03em;
      line-height: 1.1;
      margin-bottom: 12px;
      font-feature-settings: "tnum";
    }}

    .card-recovered .card-value {{ color: #34d399; }}
    .card-at-risk .card-value {{ color: #fb7185; }}
    .card-rate .card-value {{ color: #60a5fa; }}
    .card-net .card-value {{ color: #c084fc; }}

    .card-subtext {{
      font-size: 12px;
      color: var(--text-secondary);
      display: flex;
      align-items: center;
      gap: 7px;
      flex-wrap: wrap;
    }}

    .stat-pill {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 6px;
      font-weight: 600;
      font-size: 11px;
    }}

    .pill-danger {{ background: var(--danger-subtle); color: #fb7185; border: 1px solid rgba(244, 63, 94, 0.2); }}
    .pill-success {{ background: var(--success-subtle); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.2); }}
    .pill-primary {{ background: var(--primary-subtle); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.2); }}
    .pill-purple {{ background: var(--purple-subtle); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.2); }}

    /* ==========================================================================
       Charts Grid
       ========================================================================== */
    .charts-grid {{
      display: grid;
      grid-template-columns: 1.55fr 1fr;
      gap: 20px;
      margin-bottom: 36px;
    }}

    @media (max-width: 1024px) {{
      .charts-grid {{
        grid-template-columns: 1fr;
      }}
    }}

    .chart-card {{
      background: var(--card-bg);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-lg);
      padding: 24px;
      box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.4);
    }}

    .chart-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
      padding-bottom: 14px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
      flex-wrap: wrap;
      gap: 8px;
    }}

    .chart-title-box h3 {{
      font-size: 16px;
      font-weight: 700;
      color: var(--text-main);
      letter-spacing: -0.01em;
    }}

    .chart-title-box p {{
      font-size: 12px;
      color: var(--text-secondary);
      margin-top: 2px;
    }}

    .chart-container {{
      position: relative;
      height: 320px;
      width: 100%;
    }}

    /* ==========================================================================
       Section Headers & Tables
       ========================================================================== */
    .section-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      margin-bottom: 16px;
      margin-top: 40px;
      flex-wrap: wrap;
      gap: 12px;
    }}

    .section-title h2 {{
      font-size: 18px;
      font-weight: 700;
      color: var(--text-main);
      letter-spacing: -0.015em;
    }}

    .section-title p {{
      font-size: 13px;
      color: var(--text-secondary);
      margin-top: 3px;
    }}

    .table-card {{
      background: var(--card-bg);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-lg);
      overflow: hidden;
      box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.4);
      margin-bottom: 32px;
    }}

    .table-responsive {{
      width: 100%;
      overflow-x: auto;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      text-align: left;
    }}

    thead {{
      background: #0c1322;
      border-bottom: 1px solid var(--card-border);
    }}

    th {{
      padding: 14px 18px;
      font-weight: 700;
      color: var(--text-secondary);
      text-transform: uppercase;
      font-size: 11px;
      letter-spacing: 0.6px;
      white-space: nowrap;
    }}

    td {{
      padding: 14px 18px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.04);
      color: #e2e8f0;
      vertical-align: middle;
    }}

    tr:last-child td {{
      border-bottom: none;
    }}

    tbody tr {{
      transition: background-color 0.15s ease;
    }}

    tbody tr:hover {{
      background-color: rgba(59, 130, 246, 0.04);
    }}

    .code-pill {{
      background: rgba(30, 41, 59, 0.6);
      border: 1px solid rgba(255, 255, 255, 0.08);
      padding: 3px 8px;
      border-radius: 6px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
      color: #38bdf8;
      display: inline-block;
    }}

    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 9px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.2px;
      white-space: nowrap;
    }}

    .badge-dot {{
      width: 5px;
      height: 5px;
      border-radius: 50%;
      display: inline-block;
    }}
    .dot-success {{ background-color: #10b981; box-shadow: 0 0 6px #10b981; }}
    .dot-danger {{ background-color: #f43f5e; box-shadow: 0 0 6px #f43f5e; }}
    .dot-neutral {{ background-color: #94a3b8; }}

    .badge-recovered {{
      background: var(--success-subtle);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.25);
    }}

    .badge-still_failed {{
      background: var(--danger-subtle);
      color: #fb7185;
      border: 1px solid rgba(244, 63, 94, 0.25);
    }}

    .badge-correctly_stopped {{
      background: rgba(100, 116, 139, 0.15);
      color: #94a3b8;
      border: 1px solid rgba(100, 116, 139, 0.25);
    }}

    .badge-action {{
      background: var(--primary-subtle);
      color: #60a5fa;
      border: 1px solid var(--primary-border);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 11px;
    }}

    .badge-terminal {{
      background: var(--purple-subtle);
      color: #c084fc;
      border: 1px solid var(--purple-border);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 11px;
    }}

    .badge-abandoned {{
      background: var(--danger-subtle);
      color: #fb7185;
      border: 1px solid var(--danger-border);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 11px;
    }}

    /* Helper typography classes */
    .text-center {{ text-align: center; }}
    .text-right {{ text-align: right; }}
    .font-bold {{ font-weight: 700; }}
    .font-medium {{ font-weight: 500; }}
    .text-secondary {{ color: var(--text-secondary); }}
    .text-muted {{ color: var(--text-muted); }}
    .cell-mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 11px; }}
    .cell-sub-id {{ color: #f8fafc; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; }}

    /* ==========================================================================
       Subscription Audit Trail Inspector
       ========================================================================== */
    .trail-inspector-card {{
      background: var(--card-bg);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-lg);
      padding: 24px;
      margin-bottom: 32px;
      box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.4);
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }}

    .trail-inspector-card:focus-within {{
      border-color: rgba(59, 130, 246, 0.35);
      box-shadow: 0 4px 24px -2px rgba(59, 130, 246, 0.15);
    }}

    .trail-inspector-top {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 18px;
      gap: 16px;
      flex-wrap: wrap;
    }}

    .trail-title-wrap {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}

    .trail-icon-box {{
      width: 40px;
      height: 40px;
      border-radius: var(--radius-md);
      background: rgba(59, 130, 246, 0.12);
      border: 1px solid rgba(59, 130, 246, 0.25);
      color: #60a5fa;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }}

    .trail-title-wrap h3 {{
      font-size: 17px;
      font-weight: 700;
      color: var(--text-main);
      letter-spacing: -0.01em;
    }}

    .trail-title-wrap p {{
      font-size: 12px;
      color: var(--text-secondary);
      margin-top: 2px;
    }}

    .quick-sample-box {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }}

    .quick-sample-label {{
      font-size: 11px;
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}

    .sample-pill {{
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.1);
      color: #cbd5e1;
      padding: 3px 10px;
      border-radius: var(--radius-full);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 11px;
      cursor: pointer;
      transition: all 0.15s ease;
    }}

    .sample-pill:hover {{
      background: rgba(59, 130, 246, 0.15);
      border-color: rgba(59, 130, 246, 0.4);
      color: #93c5fd;
      transform: translateY(-1px);
    }}

    .trail-input-row {{
      display: flex;
      gap: 12px;
      margin-bottom: 18px;
      align-items: center;
      flex-wrap: wrap;
    }}

    .trail-input-wrapper {{
      position: relative;
      flex: 1;
      min-width: 280px;
    }}

    .trail-input {{
      width: 100%;
      background: rgba(15, 23, 42, 0.85);
      border: 1px solid var(--card-border);
      color: var(--text-main);
      padding: 12px 40px 12px 42px;
      border-radius: 10px;
      font-size: 13.5px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      outline: none;
      transition: all 0.2s ease;
    }}

    .trail-input::placeholder {{
      color: var(--text-muted);
      font-family: 'Inter', sans-serif;
      font-size: 13px;
    }}

    .trail-input:focus {{
      border-color: var(--primary);
      box-shadow: 0 0 0 3px var(--primary-glow);
      background: rgba(15, 23, 42, 0.98);
    }}

    .trail-input-icon {{
      position: absolute;
      left: 14px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-muted);
      pointer-events: none;
      display: flex;
    }}

    .clear-btn {{
      position: absolute;
      right: 12px;
      top: 50%;
      transform: translateY(-50%);
      background: rgba(255, 255, 255, 0.1);
      border: none;
      color: var(--text-secondary);
      width: 22px;
      height: 22px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 14px;
      cursor: pointer;
      transition: all 0.15s ease;
    }}

    .clear-btn:hover {{
      background: rgba(255, 255, 255, 0.2);
      color: #fff;
    }}

    .btn-action {{
      display: inline-flex;
      align-items: center;
      gap: 7px;
      padding: 11px 18px;
      border-radius: 10px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      border: none;
      transition: all 0.15s ease;
      white-space: nowrap;
    }}

    .btn-action-primary {{
      background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
      color: #ffffff;
      box-shadow: 0 4px 12px rgba(37, 99, 235, 0.35);
    }}

    .btn-action-primary:hover {{
      background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
      box-shadow: 0 6px 16px rgba(37, 99, 235, 0.45);
      transform: translateY(-1px);
    }}

    .btn-action-secondary {{
      background: rgba(255, 255, 255, 0.06);
      color: var(--text-secondary);
      border: 1px solid var(--card-border);
    }}

    .btn-action-secondary:hover {{
      background: rgba(255, 255, 255, 0.1);
      color: var(--text-main);
    }}

    /* Stepper & Pipeline Display */
    .trail-render-box {{
      background: rgba(11, 15, 25, 0.7);
      border: 1px solid rgba(255, 255, 255, 0.06);
      border-radius: var(--radius-md);
      padding: 20px;
      min-height: 130px;
      display: flex;
      flex-direction: column;
      justify-content: center;
      position: relative;
      overflow: hidden;
    }}

    .trail-placeholder-view {{
      display: flex;
      align-items: center;
      gap: 16px;
      color: var(--text-muted);
      padding: 8px 4px;
    }}

    .trail-placeholder-icon {{
      width: 44px;
      height: 44px;
      border-radius: var(--radius-md);
      background: rgba(255, 255, 255, 0.03);
      border: 1px dashed rgba(255, 255, 255, 0.15);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      color: #60a5fa;
    }}

    .trail-placeholder-text strong {{
      display: block;
      color: var(--text-secondary);
      font-size: 13.5px;
      margin-bottom: 3px;
    }}

    .trail-placeholder-text span {{
      font-size: 12.5px;
      line-height: 1.45;
      color: var(--text-muted);
    }}

    .trail-placeholder-text em {{
      color: #93c5fd;
      font-style: normal;
      font-weight: 500;
    }}

    /* Trail Match Result Card */
    .trail-match-card {{
      animation: fadeIn 0.22s ease-out;
    }}

    @keyframes fadeIn {{
      from {{ opacity: 0; transform: translateY(6px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}

    .trail-match-meta {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 18px;
      padding-bottom: 14px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
      flex-wrap: wrap;
      gap: 12px;
    }}

    .trail-id-tag {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}

    .trail-id-badge {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 14.5px;
      font-weight: 700;
      color: #60a5fa;
      background: rgba(59, 130, 246, 0.12);
      border: 1px solid rgba(59, 130, 246, 0.3);
      padding: 4px 12px;
      border-radius: 8px;
      letter-spacing: 0.5px;
    }}

    .trail-time-tag {{
      font-size: 12px;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 5px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }}

    /* Stepper Flow Layout */
    .trail-stepper {{
      display: grid;
      grid-template-columns: 1fr auto 1fr auto 1fr auto 1fr auto 1fr;
      align-items: stretch;
      gap: 8px;
    }}

    @media (max-width: 1080px) {{
      .trail-stepper {{
        grid-template-columns: 1fr;
        gap: 12px;
      }}
      .trail-step-connector {{
        transform: rotate(90deg);
        justify-content: center;
        padding: 4px 0;
      }}
    }}

    .trail-step-card {{
      background: rgba(17, 24, 39, 0.85);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: var(--radius-md);
      padding: 14px 12px;
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      position: relative;
      transition: all 0.2s ease;
      min-width: 0;
    }}

    .trail-step-card:hover {{
      border-color: rgba(255, 255, 255, 0.18);
      transform: translateY(-2px);
      box-shadow: 0 6px 14px -3px rgba(0, 0, 0, 0.5);
    }}

    .step-badge-num {{
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: var(--text-muted);
      margin-bottom: 6px;
    }}

    .step-card-title {{
      font-size: 11px;
      font-weight: 600;
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.4px;
      margin-bottom: 10px;
    }}

    .step-card-value {{
      font-size: 12.5px;
      font-weight: 600;
      color: var(--text-main);
      margin-bottom: 8px;
      word-break: break-word;
      display: flex;
      align-items: center;
      justify-content: center;
      flex: 1;
    }}

    .step-card-sub {{
      font-size: 10.5px;
      color: var(--text-muted);
    }}

    .trail-step-connector {{
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--text-muted);
    }}

    .trail-step-connector svg {{
      opacity: 0.6;
    }}

    /* Step Accent Highlights */
    .step-accent-issue {{
      border-top: 2px solid #f59e0b;
    }}
    .step-accent-cause {{
      border-top: 2px solid #38bdf8;
    }}
    .step-accent-action {{
      border-top: 2px solid #a855f7;
    }}
    .step-accent-attempt {{
      border-top: 2px solid #6366f1;
    }}
    .step-accent-recovered {{
      border-top: 2px solid #10b981;
      background: rgba(16, 185, 129, 0.05);
    }}
    .step-accent-still_failed {{
      border-top: 2px solid #f43f5e;
      background: rgba(244, 63, 94, 0.05);
    }}
    .step-accent-correctly_stopped {{
      border-top: 2px solid #94a3b8;
      background: rgba(100, 116, 139, 0.05);
    }}

    .trail-not-found-view {{
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 10px 4px;
    }}

    .not-found-icon-box {{
      width: 44px;
      height: 44px;
      border-radius: var(--radius-md);
      background: rgba(244, 63, 94, 0.1);
      border: 1px solid rgba(244, 63, 94, 0.25);
      color: #fb7185;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }}

    .not-found-text h4 {{
      font-size: 14px;
      font-weight: 700;
      color: #f8fafc;
      margin-bottom: 3px;
    }}

    .not-found-text p {{
      font-size: 12.5px;
      color: var(--text-secondary);
    }}

    /* Clickable sub ID in table */
    .btn-sub-link {{
      background: transparent;
      border: none;
      color: #38bdf8;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px 7px;
      border-radius: 5px;
      transition: all 0.15s ease;
    }}

    .btn-sub-link:hover {{
      background: rgba(56, 189, 248, 0.12);
      color: #7dd3fc;
    }}

    .btn-sub-link svg {{
      opacity: 0.6;
      transition: opacity 0.15s ease;
    }}

    .btn-sub-link:hover svg {{
      opacity: 1;
    }}

    .btn-filter-reset {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 9px 14px;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      color: var(--text-secondary);
      font-size: 12.5px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.15s ease;
      white-space: nowrap;
    }}

    .btn-filter-reset:hover {{
      background: rgba(255, 255, 255, 0.1);
      color: var(--text-main);
      border-color: rgba(255, 255, 255, 0.2);
    }}

    .table-count-pill {{
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.08);
      padding: 4px 10px;
      border-radius: var(--radius-full);
    }}

    /* ==========================================================================
       Audit Log & Controls
       ========================================================================== */
    .search-bar {{
      display: flex;
      gap: 12px;
      margin-bottom: 16px;
      flex-wrap: wrap;
      align-items: center;
    }}

    .search-wrapper {{
      position: relative;
      flex: 1;
      min-width: 260px;
    }}

    .search-input {{
      width: 100%;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text-main);
      padding: 10px 14px 10px 38px;
      border-radius: 10px;
      font-size: 13px;
      outline: none;
      transition: all 0.2s ease;
    }}

    .search-input::placeholder {{
      color: var(--text-muted);
    }}

    .search-icon {{
      position: absolute;
      left: 12px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-muted);
      pointer-events: none;
      display: flex;
      align-items: center;
    }}

    .search-input:focus {{
      border-color: var(--primary);
      box-shadow: 0 0 0 3px var(--primary-glow);
      background: rgba(15, 23, 42, 0.95);
    }}

    .filter-select {{
      appearance: none;
      -webkit-appearance: none;
      background-color: var(--card-bg);
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 12px center;
      border: 1px solid var(--card-border);
      color: var(--text-main);
      padding: 10px 36px 10px 14px;
      border-radius: 10px;
      font-size: 13px;
      outline: none;
      cursor: pointer;
      transition: all 0.2s ease;
    }}

    .filter-select:hover {{
      border-color: rgba(255, 255, 255, 0.2);
      background-color: rgba(255, 255, 255, 0.05);
    }}

    .filter-select:focus {{
      border-color: var(--primary);
      box-shadow: 0 0 0 3px var(--primary-glow);
    }}

    .filter-select option {{
      background: #0f172a;
      color: #f8fafc;
    }}

    .audit-container {{
      max-height: 520px;
      overflow-y: auto;
    }}

    .audit-container thead th {{
      position: sticky;
      top: 0;
      z-index: 5;
      background: #0c1322;
      box-shadow: 0 1px 0 var(--card-border);
    }}

    .audit-container::-webkit-scrollbar {{
      width: 6px;
    }}
    .audit-container::-webkit-scrollbar-track {{
      background: transparent;
    }}
    .audit-container::-webkit-scrollbar-thumb {{
      background: rgba(255, 255, 255, 0.15);
      border-radius: var(--radius-full);
    }}
    .audit-container::-webkit-scrollbar-thumb:hover {{
      background: rgba(255, 255, 255, 0.3);
    }}

    /* ==========================================================================
       Footer
       ========================================================================== */
    footer {{
      margin-top: 56px;
      padding-top: 24px;
      border-top: 1px solid var(--card-border);
      color: var(--text-muted);
      font-size: 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
    }}

    .footer-left {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    .footer-left strong {{
      color: var(--text-secondary);
    }}

    .footer-right {{
      color: var(--text-muted);
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand-section">
        <div class="brand-icon-box">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
            <path d="m9 12 2 2 4-4"/>
          </svg>
        </div>
        <div class="title-group">
          <div class="kicker-row">
            <div class="kicker">Razorpay AI Buildathon &bull; Track 03</div>
            <div class="live-pulse"><span class="pulse-dot"></span> Live Pipeline</div>
          </div>
          <h1>
            <span class="title-gradient">AI Revenue Recovery Agent</span>
          </h1>
          <p class="title-sub">Autonomous, compliance-bounded subscription payment diagnosis & smart recovery pipeline</p>
        </div>
      </div>
      <div class="header-actions">
        <div class="badges-row">
          <span class="badge-pill badge-compliance">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            RBI & NPCI Compliant (T+7)
          </span>
          <span class="badge-pill badge-batch">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>
            Batch: {summary_metrics.get('total_records', 700)} Records
          </span>
          <span id="sourceBadge" class="badge-pill badge-source">Checking data source...</span>
        </div>
        <div class="controls-row">
          <label class="btn-upload">
            <input type="file" id="filePicker" multiple accept=".json,.csv" style="display: none;" onchange="handleFileSelect(event)">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
            Choose Local JSON/CSV
          </label>
          <span class="timestamp-tag">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            Run: <strong>{audit_rows[0].get('timestamp', 'Latest') if audit_rows else 'Latest'}</strong>
          </span>
        </div>
      </div>
    </header>

    <div class="metrics-grid">
      <div class="card card-at-risk">
        <div class="card-header-row">
          <div class="card-label">Total Revenue at Risk</div>
          <div class="card-icon-pill">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          </div>
        </div>
        <div class="card-value" id="val-at-risk">₹{summary_metrics.get('total_at_risk_inr', 0):,}</div>
        <div class="card-subtext">
          <span class="stat-pill pill-danger">{summary_metrics.get('total_failed', 0)} failed</span>
          <span>{(summary_metrics.get('total_failed', 0)/summary_metrics.get('total_records', 1)*100):.1f}% failure rate across batch</span>
        </div>
      </div>
      <div class="card card-recovered">
        <div class="card-header-row">
          <div class="card-label">Revenue Recovered</div>
          <div class="card-icon-pill">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect width="20" height="14" x="2" y="5" rx="2"/><line x1="2" x2="22" y1="10" y2="10"/><path d="m9 16 2 2 4-4"/></svg>
          </div>
        </div>
        <div class="card-value" id="val-recovered">₹{summary_metrics.get('recovered_amount_inr', 0):,}</div>
        <div class="card-subtext">
          <span class="stat-pill pill-success">{summary_metrics.get('recovered_count', 0)} recovered</span>
          <span>successfully restored subscriptions</span>
        </div>
      </div>
      <div class="card card-rate">
        <div class="card-header-row">
          <div class="card-label">Recovery Rate</div>
          <div class="card-icon-pill">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>
          </div>
        </div>
        <div class="card-value" id="val-rate">{summary_metrics.get('recovery_rate_pct', 0):.1f}%</div>
        <div class="card-subtext">
          <span class="stat-pill pill-primary">Automated</span>
          <span>single batch recovery cycle yield</span>
        </div>
      </div>
      <div class="card card-net">
        <div class="card-header-row">
          <div class="card-label">Net Recovery Value</div>
          <div class="card-icon-pill">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>
          </div>
        </div>
        <div class="card-value" id="val-net">₹{summary_metrics.get('net_recovery_value_inr', 0):,}</div>
        <div class="card-subtext">
          <span class="stat-pill pill-purple">Net yield</span>
          <span>less ₹{summary_metrics.get('false_positive_cost_inr', 0)} wasted retry costs</span>
        </div>
      </div>
    </div>

    <div class="charts-grid">
      <div class="chart-card">
        <div class="chart-header">
          <div class="chart-title-box">
            <h3>Revenue Impact Breakdown (₹ INR)</h3>
            <p>At-Risk vs Recovered vs Missed Opportunity vs Unrecoverable</p>
          </div>
          <span class="badge-pill badge-batch">Financial Flow</span>
        </div>
        <div class="chart-container">
          <canvas id="revenueBarChart"></canvas>
        </div>
      </div>
      <div class="chart-card">
        <div class="chart-header">
          <div class="chart-title-box">
            <h3>Execution Outcomes</h3>
            <p>{summary_metrics.get('total_failed', 0)} Diagnosed & Processed Records</p>
          </div>
          <span class="badge-pill badge-compliance">Distribution</span>
        </div>
        <div class="chart-container">
          <canvas id="outcomeDonutChart"></canvas>
        </div>
      </div>
    </div>

    <div class="section-header">
      <div class="section-title">
        <h2>Failure Reason Breakdown & Recovery Performance</h2>
        <p>Granular telemetry aggregated across all diagnosed payment failure codes</p>
      </div>
    </div>
    <div class="table-card">
      <div class="table-responsive">
        <table>
          <thead>
            <tr>
              <th>Failure Reason Code</th>
              <th>Diagnosed Cause Category</th>
              <th class="text-center">Total Failed</th>
              <th class="text-right">Revenue at Risk (₹)</th>
              <th class="text-center">Recovered</th>
              <th class="text-center">Still Failed</th>
              <th class="text-center">Correctly Stopped</th>
              <th class="text-right">Revenue Recovered (₹)</th>
              <th class="text-right">Recovery Rate</th>
            </tr>
          </thead>
          <tbody id="reasonTableBody"></tbody>
        </table>
      </div>
    </div>

    <!-- Subscription Audit Trail Inspector -->
    <div class="section-header" id="trailInspectorSection">
      <div class="section-title">
        <h2>Subscription Audit Trail Inspector</h2>
        <p>Instant forensic trace: Detected Issue &rarr; Diagnosed Cause &rarr; Chosen Action &rarr; Retry Attempt &rarr; Final Outcome</p>
      </div>
    </div>

    <div class="trail-inspector-card" id="trailInspectorCard">
      <div class="trail-inspector-top">
        <div class="trail-title-wrap">
          <div class="trail-icon-box">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="11" cy="11" r="8"></circle>
              <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
            </svg>
          </div>
          <div>
            <h3>Subscription Lifecycle Trace</h3>
            <p>End-to-end execution path pulled directly from verified audit records</p>
          </div>
        </div>
        <div class="quick-sample-box">
          <span class="quick-sample-label">Quick samples:</span>
          <span id="quickSampleChips"></span>
        </div>
      </div>

      <div class="trail-input-row">
        <div class="trail-input-wrapper">
          <span class="trail-input-icon">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          </span>
          <input type="text" id="subLookupInput" class="trail-input" placeholder="Enter Subscription ID to trace (e.g. sub_100028, sub_100031)..." oninput="handleSubLookup(false)" onkeydown="if(event.key==='Enter') handleSubLookup(true)" />
          <button type="button" id="subLookupClearBtn" class="clear-btn" onclick="clearSubLookup()" style="display:none;" title="Clear search">&times;</button>
        </div>
        <button type="button" class="btn-action btn-action-primary" onclick="handleSubLookup(true)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
          Trace Trail
        </button>
        <button type="button" class="btn-action btn-action-secondary" onclick="clearSubLookup()">
          Reset
        </button>
      </div>

      <div id="trailDisplayArea" class="trail-render-box">
        <!-- Rendered dynamically -->
      </div>
    </div>

    <!-- Auditable Execution Trail Ledger -->
    <div class="section-header">
      <div class="section-title">
        <h2>Auditable Execution Trail</h2>
        <p>Immutable ledger of automated agent decisions, intervention steps & final transaction states</p>
      </div>
      <div class="table-count-pill" id="tableRecordCount">
        Showing records
      </div>
    </div>
    <div class="search-bar">
      <div class="search-wrapper">
        <span class="search-icon">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        </span>
        <input type="text" id="searchInput" class="search-input" placeholder="Search table by ID, reason, or action..." onkeyup="filterAuditTable()" />
      </div>

      <select id="reasonFilter" class="filter-select" onchange="filterAuditTable()" title="Filter by failure reason">
        <option value="">All Failure Reasons</option>
        <option value="bank_technical_decline">bank_technical_decline</option>
        <option value="instrument_expired">instrument_expired</option>
        <option value="insufficient_funds">insufficient_funds</option>
        <option value="mandate_expired">mandate_expired</option>
        <option value="mandate_revoked">mandate_revoked</option>
        <option value="risk_block">risk_block</option>
      </select>

      <select id="outcomeFilter" class="filter-select" onchange="filterAuditTable()" title="Filter by outcome">
        <option value="">All Outcomes</option>
        <option value="recovered">Recovered</option>
        <option value="still_failed">Still Failed</option>
        <option value="correctly_stopped">Correctly Stopped</option>
      </select>

      <select id="actionFilter" class="filter-select" onchange="filterAuditTable()" title="Filter by action">
        <option value="">All Actions</option>
        <option value="retry_scheduled">retry_scheduled</option>
        <option value="reauth_request">reauth_request</option>
        <option value="no_retry_winback_offer">no_retry_winback_offer</option>
        <option value="immediate_retry_once">immediate_retry_once</option>
        <option value="update_payment_method_request">update_payment_method_request</option>
        <option value="manual_review_escalation">manual_review_escalation</option>
        <option value="recovery_abandoned">recovery_abandoned</option>
      </select>

      <button type="button" class="btn-filter-reset" onclick="resetAuditFilters()" title="Reset all table filters">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
        Reset
      </button>
    </div>
    <div class="table-card">
      <div class="audit-container">
        <table>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Subscription ID</th>
              <th>Detected Issue</th>
              <th>Diagnosed Cause</th>
              <th>Chosen Action</th>
              <th class="text-center">Attempt #</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody id="auditTableBody"></tbody>
        </table>
      </div>
    </div>

    <footer>
      <div class="footer-left">
        <strong>Razorpay AI Buildathon — Track 03:</strong> Intelligent Revenue Recovery Agent
      </div>
      <div class="footer-right">
        Compliance Guardrails: RBI e-Mandate Circular DPSS.CO.PD.No.447/02.14.003 & NPCI NACH/UPI Guidelines
      </div>
    </footer>
  </div>

  <script>
    const EMBEDDED_METRICS = {json.dumps(summary_metrics)};
    const EMBEDDED_REASONS = {json.dumps(reason_breakdown)};
    const EMBEDDED_AUDIT = {json.dumps(audit_rows)};

    let currentMetrics = EMBEDDED_METRICS;
    let currentAudit = EMBEDDED_AUDIT;
    let currentReasons = EMBEDDED_REASONS;
    let barChartInstance = null;
    let donutChartInstance = null;

    function formatINR(val) {{
      return '₹' + Number(val).toLocaleString('en-IN');
    }}

    // Try reading live from the actual files
    async function loadLiveData() {{
      let liveMetricsLoaded = false;
      let liveAuditLoaded = false;

      try {{
        const resp = await fetch('metrics_summary.json?nocache=' + Date.now());
        if (resp.ok) {{
          currentMetrics = await resp.json();
          liveMetricsLoaded = true;
          console.log('[Dashboard] Successfully loaded live metrics_summary.json');
        }}
      }} catch (err) {{
        console.warn('[Dashboard] Live fetch for metrics_summary.json blocked (likely file:// origin). Using embedded snapshot.', err);
      }}

      try {{
        const resp = await fetch('audit_log.csv?nocache=' + Date.now());
        if (resp.ok) {{
          const text = await resp.text();
          currentAudit = parseCSV(text);
          liveAuditLoaded = true;
          console.log('[Dashboard] Successfully loaded live audit_log.csv (' + currentAudit.length + ' rows)');
        }}
      }} catch (err) {{
        console.warn('[Dashboard] Live fetch for audit_log.csv blocked (likely file:// origin). Using embedded snapshot.', err);
      }}

      // Update source indicator badge
      const badge = document.getElementById('sourceBadge');
      if (badge) {{
        if (liveMetricsLoaded && liveAuditLoaded) {{
          badge.className = 'badge-pill badge-batch';
          badge.innerHTML = '🟢 Live Source: HTTP fetch';
          // Re-render with fresh live data
          initDashboard();
        }} else {{
          badge.className = 'badge-pill badge-compliance';
          badge.innerHTML = '🔵 Local Snapshot';
        }}
      }}
    }}

    function parseCSV(text) {{
      var CR = String.fromCharCode(13), LF = String.fromCharCode(10);
      const lines = text.split(CR).join('').split(LF);
      if (lines.length <= 1) return [];
      const headers = lines[0].split(',').map(h => h.trim());
      const records = [];
      for (let i = 1; i < lines.length; i++) {{
        const row = lines[i].split(',').map(c => c.trim());
        if (row.length >= headers.length) {{
          const item = {{}};
          headers.forEach((h, idx) => item[h] = row[idx]);
          records.push(item);
        }}
      }}
      return records;
    }}

    // Allow user to select fresh files directly from disk even on file:// protocol
    function handleFileSelect(event) {{
      const files = event.target.files;
      for (let i = 0; i < files.length; i++) {{
        const file = files[i];
        const reader = new FileReader();
        if (file.name.endsWith('.json')) {{
          reader.onload = function(e) {{
            try {{
              currentMetrics = JSON.parse(e.target.result);
              document.getElementById('sourceBadge').innerHTML = '🟣 Selected: ' + file.name;
              initDashboard();
            }} catch (err) {{
              alert('Error parsing JSON: ' + err.message);
            }}
          }};
          reader.readAsText(file);
        }} else if (file.name.endsWith('.csv')) {{
          reader.onload = function(e) {{
            currentAudit = parseCSV(e.target.result);
            document.getElementById('sourceBadge').innerHTML = '🟣 Selected: ' + file.name;
            initDashboard();
          }};
          reader.readAsText(file);
        }}
      }}
    }}

    function initDashboard() {{
      // Update Metric Cards (if live data loaded; otherwise keep embedded values)
      const elAtRisk = document.getElementById('val-at-risk');
      const elRecovered = document.getElementById('val-recovered');
      const elRate = document.getElementById('val-rate');
      const elNet = document.getElementById('val-net');
      if (elAtRisk && currentMetrics.total_at_risk_inr !== undefined) {{
        elAtRisk.innerText = formatINR(currentMetrics.total_at_risk_inr);
      }}
      if (elRecovered && currentMetrics.recovered_amount_inr !== undefined) {{
        elRecovered.innerText = formatINR(currentMetrics.recovered_amount_inr);
      }}
      if (elRate && currentMetrics.recovery_rate_pct !== undefined) {{
        elRate.innerText = currentMetrics.recovery_rate_pct.toFixed(1) + '%';
      }}
      if (elNet && currentMetrics.net_recovery_value_inr !== undefined) {{
        elNet.innerText = formatINR(currentMetrics.net_recovery_value_inr);
      }}

      renderCharts();
      renderReasonTable();
      renderAuditTable(currentAudit);
      updateRecordCount(currentAudit.length, currentAudit.length);
      renderTrailPlaceholder();
      initQuickSamples();
    }}

    function renderCharts() {{
      const atRisk = currentMetrics.total_at_risk_inr;
      const recovered = currentMetrics.recovered_amount_inr;
      const missed = currentMetrics.missed_recovery_amount_inr;
      const unrecoverable = atRisk - recovered - missed;

      // Global chart defaults
      if (window.Chart) {{
        Chart.defaults.font.family = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
        Chart.defaults.color = '#94a3b8';
      }}

      const customTooltip = {{
        backgroundColor: 'rgba(15, 23, 42, 0.95)',
        titleColor: '#f8fafc',
        bodyColor: '#e2e8f0',
        borderColor: 'rgba(255, 255, 255, 0.1)',
        borderWidth: 1,
        padding: 12,
        boxPadding: 6,
        usePointStyle: true,
        cornerRadius: 8,
      }};

      const ctxBar = document.getElementById('revenueBarChart').getContext('2d');
      if (barChartInstance) barChartInstance.destroy();
      barChartInstance = new Chart(ctxBar, {{
        type: 'bar',
        data: {{
          labels: ['Revenue at Risk', 'Revenue Recovered', 'Missed Opportunities', 'Unrecoverable (Stopped)'],
          datasets: [{{
            label: 'Amount (₹)',
            data: [atRisk, recovered, missed, unrecoverable],
            backgroundColor: [
              'rgba(244, 63, 94, 0.85)',
              'rgba(16, 185, 129, 0.85)',
              'rgba(245, 158, 11, 0.85)',
              'rgba(100, 116, 139, 0.85)'
            ],
            borderColor: ['#f43f5e', '#10b981', '#f59e0b', '#64748b'],
            borderWidth: 1.5,
            borderRadius: 8,
            hoverBackgroundColor: [
              'rgba(244, 63, 94, 1)',
              'rgba(16, 185, 129, 1)',
              'rgba(245, 158, 11, 1)',
              'rgba(100, 116, 139, 1)'
            ]
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{
              ...customTooltip,
              callbacks: {{
                label: (ctx) => ' Amount: ' + formatINR(ctx.raw)
              }}
            }}
          }},
          scales: {{
            y: {{
              beginAtZero: true,
              grid: {{ color: 'rgba(255, 255, 255, 0.04)', drawBorder: false }},
              ticks: {{
                color: '#94a3b8',
                callback: (v) => '₹' + (v >= 1000 ? (v / 1000) + 'k' : v)
              }}
            }},
            x: {{
              grid: {{ display: false, drawBorder: false }},
              ticks: {{ color: '#94a3b8' }}
            }}
          }}
        }}
      }});

      const ctxDonut = document.getElementById('outcomeDonutChart').getContext('2d');
      if (donutChartInstance) donutChartInstance.destroy();
      donutChartInstance = new Chart(ctxDonut, {{
        type: 'doughnut',
        data: {{
          labels: [
            `Recovered (${{currentMetrics.recovered_count}})`,
            `Correctly Stopped (${{currentMetrics.correctly_stopped_count}})`,
            `Still Failed (${{currentMetrics.still_failed_count}})`
          ],
          datasets: [{{
            data: [
              currentMetrics.recovered_count,
              currentMetrics.correctly_stopped_count,
              currentMetrics.still_failed_count
            ],
            backgroundColor: ['#10b981', '#64748b', '#f43f5e'],
            borderWidth: 2,
            borderColor: '#0f172a',
            hoverOffset: 6
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{
              position: 'bottom',
              labels: {{ color: '#94a3b8', boxWidth: 10, boxHeight: 10, usePointStyle: true, padding: 18 }}
            }},
            tooltip: {{
              ...customTooltip,
              callbacks: {{
                label: (ctx) => ' ' + ctx.label + ': ' + ctx.raw + ' (' + ((ctx.raw / currentMetrics.total_failed) * 100).toFixed(1) + '%)'
              }}
            }}
          }},
          cutout: '72%'
        }}
      }});
    }}

    function renderReasonTable() {{
      const tbody = document.getElementById('reasonTableBody');
      const causeMap = {{"insufficient_funds": "funding_shortfall", "mandate_expired": "mandate_lifecycle", "mandate_revoked": "mandate_lifecycle", "bank_technical_decline": "transient_bank_error", "instrument_expired": "payment_instrument_issue", "risk_block": "fraud_or_risk_hold"}};

      let html = '';
      for (const [reason, d] of Object.entries(currentReasons)) {{
        const rateNum = d.amount_at_risk > 0 ? ((d.amount_recovered / d.amount_at_risk) * 100).toFixed(1) : '0.0';
        const isRecovered = d.amount_recovered > 0;
        html += `<tr>
          <td><code class="code-pill">${{reason}}</code></td>
          <td><span class="text-secondary">${{causeMap[reason] || '-'}}</span></td>
          <td class="text-center font-bold">${{d.total}}</td>
          <td class="text-right font-medium">${{formatINR(d.amount_at_risk)}}</td>
          <td class="text-center"><span class="badge badge-recovered"><span class="badge-dot dot-success"></span>${{d.recovered}}</span></td>
          <td class="text-center"><span class="badge badge-still_failed"><span class="badge-dot dot-danger"></span>${{d.still_failed}}</span></td>
          <td class="text-center"><span class="badge badge-correctly_stopped"><span class="badge-dot dot-neutral"></span>${{d.correctly_stopped}}</span></td>
          <td class="text-right font-medium" style="color: ${{isRecovered ? '#34d399' : 'inherit'}};">${{formatINR(d.amount_recovered)}}</td>
          <td class="text-right"><strong style="color: ${{isRecovered ? '#34d399' : '#94a3b8'}};">${{rateNum}}%</strong></td>
        </tr>`;
      }}
      tbody.innerHTML = html;
    }}

    function escapeHtml(str) {{
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }}

    function renderTrailPlaceholder() {{
      const renderArea = document.getElementById('trailDisplayArea');
      if (!renderArea) return;
      renderArea.innerHTML = `
        <div class="trail-placeholder-view">
          <div class="trail-placeholder-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="11" cy="11" r="8"></circle>
              <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
            </svg>
          </div>
          <div class="trail-placeholder-text">
            <strong>Forensic Trail Inspector Ready</strong>
            <span>Enter a subscription ID above or click any ID in the table below to inspect its full decision trail: <em>detected issue &rarr; diagnosed cause &rarr; chosen action &rarr; retry attempt number &rarr; final outcome</em>.</span>
          </div>
        </div>
      `;
    }}

    function renderTrailNotFound(query) {{
      const renderArea = document.getElementById('trailDisplayArea');
      if (!renderArea) return;
      renderArea.innerHTML = `
        <div class="trail-not-found-view">
          <div class="not-found-icon-box">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="15" y1="9" x2="9" y2="15"></line>
              <line x1="9" y1="9" x2="15" y2="15"></line>
            </svg>
          </div>
          <div class="not-found-text">
            <h4>No Audit Record Found for "${{escapeHtml(query)}}"</h4>
            <p>No matching record exists in the current audit dataset. Please verify the ID or try clicking one of the sample pills above.</p>
          </div>
        </div>
      `;
    }}

    function renderTrailMatch(r) {{
      const renderArea = document.getElementById('trailDisplayArea');
      if (!renderArea) return;

      let outcomeBadge = '<span class="badge badge-still_failed"><span class="badge-dot dot-danger"></span>still_failed</span>';
      let outcomeClass = 'step-accent-still_failed';
      if (r.outcome === 'recovered') {{
        outcomeBadge = '<span class="badge badge-recovered"><span class="badge-dot dot-success"></span>recovered</span>';
        outcomeClass = 'step-accent-recovered';
      }} else if (r.outcome === 'correctly_stopped') {{
        outcomeBadge = '<span class="badge badge-correctly_stopped"><span class="badge-dot dot-neutral"></span>correctly_stopped</span>';
        outcomeClass = 'step-accent-correctly_stopped';
      }}

      let actionBadgeClass = 'badge-action';
      if (r.chosen_action === 'recovery_abandoned') {{
        actionBadgeClass = 'badge-abandoned';
      }} else if (r.chosen_action === 'no_retry_winback_offer' || r.chosen_action === 'manual_review_escalation') {{
        actionBadgeClass = 'badge-terminal';
      }}

      const attemptDisplay = (r.retry_attempt_number !== undefined && r.retry_attempt_number !== null && r.retry_attempt_number !== '') 
        ? `#${{r.retry_attempt_number}}` 
        : 'None (Terminal)';

      renderArea.innerHTML = `
        <div class="trail-match-card">
          <div class="trail-match-meta">
            <div class="trail-id-tag">
              <span class="text-secondary cell-mono font-bold" style="letter-spacing: 0.5px;">SUBSCRIPTION:</span>
              <span class="trail-id-badge">${{escapeHtml(r.subscription_id)}}</span>
              <span class="trail-time-tag">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                ${{escapeHtml(r.timestamp)}}
              </span>
            </div>
            <div>
              ${{outcomeBadge}}
            </div>
          </div>

          <div class="trail-stepper">
            <!-- STEP 1: Detected Issue -->
            <div class="trail-step-card step-accent-issue">
              <div class="step-badge-num">Stage 1</div>
              <div class="step-card-title">Detected Issue</div>
              <div class="step-card-value">
                <code class="code-pill">${{escapeHtml(r.detected_issue)}}</code>
              </div>
              <div class="step-card-sub">Initial Failure Code</div>
            </div>

            <div class="trail-step-connector">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
            </div>

            <!-- STEP 2: Diagnosed Cause -->
            <div class="trail-step-card step-accent-cause">
              <div class="step-badge-num">Stage 2</div>
              <div class="step-card-title">Diagnosed Cause</div>
              <div class="step-card-value">
                <span style="color: #38bdf8;">${{escapeHtml(r.diagnosed_cause)}}</span>
              </div>
              <div class="step-card-sub">Root Cause Category</div>
            </div>

            <div class="trail-step-connector">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
            </div>

            <!-- STEP 3: Chosen Action -->
            <div class="trail-step-card step-accent-action">
              <div class="step-badge-num">Stage 3</div>
              <div class="step-card-title">Chosen Action</div>
              <div class="step-card-value">
                <span class="badge ${{actionBadgeClass}}">${{escapeHtml(r.chosen_action)}}</span>
              </div>
              <div class="step-card-sub">Intervention Policy</div>
            </div>

            <div class="trail-step-connector">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
            </div>

            <!-- STEP 4: Retry Attempt Number -->
            <div class="trail-step-card step-accent-attempt">
              <div class="step-badge-num">Stage 4</div>
              <div class="step-card-title">Retry Attempt #</div>
              <div class="step-card-value">
                <span class="code-pill" style="color: #a5b4fc;">${{attemptDisplay}}</span>
              </div>
              <div class="step-card-sub">Attempt Number</div>
            </div>

            <div class="trail-step-connector">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
            </div>

            <!-- STEP 5: Final Outcome -->
            <div class="trail-step-card ${{outcomeClass}}">
              <div class="step-badge-num">Stage 5</div>
              <div class="step-card-title">Final Outcome</div>
              <div class="step-card-value">
                ${{outcomeBadge}}
              </div>
              <div class="step-card-sub">Final Ledger State</div>
            </div>
          </div>
        </div>
      `;
    }}

    function handleSubLookup(scrollOnMatch = false) {{
      const inputEl = document.getElementById('subLookupInput');
      if (!inputEl) return;
      const rawInput = inputEl.value.trim();
      const clearBtn = document.getElementById('subLookupClearBtn');

      if (clearBtn) {{
        clearBtn.style.display = rawInput ? 'flex' : 'none';
      }}

      if (!rawInput) {{
        renderTrailPlaceholder();
        return;
      }}

      const cleanQuery = rawInput.toLowerCase();
      // Look for exact match first
      let match = currentAudit.find(r => r.subscription_id && r.subscription_id.toLowerCase() === cleanQuery);
      
      // If not exact match, look for prefix/substring match
      if (!match) {{
        match = currentAudit.find(r => r.subscription_id && r.subscription_id.toLowerCase().includes(cleanQuery));
      }}

      if (match) {{
        renderTrailMatch(match);
        if (scrollOnMatch) {{
          const card = document.getElementById('trailInspectorCard');
          if (card) {{
            card.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
          }}
        }}
      }} else {{
        renderTrailNotFound(rawInput);
      }}
    }}

    function clearSubLookup() {{
      const inputEl = document.getElementById('subLookupInput');
      if (inputEl) {{
        inputEl.value = '';
        inputEl.focus();
      }}
      handleSubLookup(false);
    }}

    function inspectSubscription(subId) {{
      const inputEl = document.getElementById('subLookupInput');
      if (inputEl) {{
        inputEl.value = subId;
        handleSubLookup(true);
        const card = document.getElementById('trailInspectorCard');
        if (card) {{
          card.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
        }}
      }}
    }}

    function initQuickSamples() {{
      const container = document.getElementById('quickSampleChips');
      if (!container || !currentAudit || currentAudit.length === 0) return;

      const samples = [];
      const rec = currentAudit.find(r => r.outcome === 'recovered');
      if (rec) samples.push({{ id: rec.subscription_id, label: `${{rec.subscription_id}} (recovered)` }});
      const failed = currentAudit.find(r => r.outcome === 'still_failed');
      if (failed && !samples.some(s => s.id === failed.subscription_id)) {{
        samples.push({{ id: failed.subscription_id, label: `${{failed.subscription_id}} (failed)` }});
      }}
      const stopped = currentAudit.find(r => r.outcome === 'correctly_stopped');
      if (stopped && !samples.some(s => s.id === stopped.subscription_id)) {{
        samples.push({{ id: stopped.subscription_id, label: `${{stopped.subscription_id}} (stopped)` }});
      }}

      container.innerHTML = samples.map(s => 
        `<button type="button" class="sample-pill" onclick="inspectSubscription('${{s.id}}')">${{s.label}}</button>`
      ).join('');
    }}

    function updateRecordCount(count, total) {{
      const el = document.getElementById('tableRecordCount');
      if (el) {{
        if (count === total) {{
          el.textContent = `Showing all ${{total}} records`;
        }} else {{
          el.textContent = `Showing ${{count}} of ${{total}} records`;
        }}
      }}
    }}

    function renderAuditTable(records) {{
      const tbody = document.getElementById('auditTableBody');
      let html = '';
      for (const r of records) {{
        let outcomeBadge = '<span class="badge badge-still_failed"><span class="badge-dot dot-danger"></span>still_failed</span>';
        if (r.outcome === 'recovered') {{
          outcomeBadge = '<span class="badge badge-recovered"><span class="badge-dot dot-success"></span>recovered</span>';
        }} else if (r.outcome === 'correctly_stopped') {{
          outcomeBadge = '<span class="badge badge-correctly_stopped"><span class="badge-dot dot-neutral"></span>correctly_stopped</span>';
        }}

        let actionBadgeClass = 'badge-action';
        if (r.chosen_action === 'recovery_abandoned') {{
          actionBadgeClass = 'badge-abandoned';
        }} else if (r.chosen_action === 'no_retry_winback_offer' || r.chosen_action === 'manual_review_escalation') {{
          actionBadgeClass = 'badge-terminal';
        }}

        html += `<tr>
          <td class="cell-mono text-muted">${{r.timestamp}}</td>
          <td>
            <button type="button" class="btn-sub-link" onclick="inspectSubscription('${{r.subscription_id}}')" title="Inspect full audit trail for ${{r.subscription_id}}">
              ${{r.subscription_id}}
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>
            </button>
          </td>
          <td><code class="code-pill">${{r.detected_issue}}</code></td>
          <td class="text-secondary">${{r.diagnosed_cause}}</td>
          <td><span class="badge ${{actionBadgeClass}}">${{r.chosen_action}}</span></td>
          <td class="text-center text-muted">${{r.retry_attempt_number || '—'}}</td>
          <td>${{outcomeBadge}}</td>
        </tr>`;
      }}
      tbody.innerHTML = html;
    }}

    function filterAuditTable() {{
      const search = document.getElementById('searchInput').value.toLowerCase().trim();
      const reasonVal = document.getElementById('reasonFilter').value;
      const outcomeVal = document.getElementById('outcomeFilter').value;
      const actionVal = document.getElementById('actionFilter').value;

      const filtered = currentAudit.filter(r => {{
        const matchesSearch = !search || 
          (r.subscription_id && r.subscription_id.toLowerCase().includes(search)) ||
          (r.detected_issue && r.detected_issue.toLowerCase().includes(search)) ||
          (r.diagnosed_cause && r.diagnosed_cause.toLowerCase().includes(search)) ||
          (r.chosen_action && r.chosen_action.toLowerCase().includes(search));
        const matchesReason = !reasonVal || r.detected_issue === reasonVal;
        const matchesOutcome = !outcomeVal || r.outcome === outcomeVal;
        const matchesAction = !actionVal || r.chosen_action === actionVal;
        return matchesSearch && matchesReason && matchesOutcome && matchesAction;
      }});

      renderAuditTable(filtered);
      updateRecordCount(filtered.length, currentAudit.length);
    }}

    function resetAuditFilters() {{
      document.getElementById('searchInput').value = '';
      document.getElementById('reasonFilter').value = '';
      document.getElementById('outcomeFilter').value = '';
      document.getElementById('actionFilter').value = '';
      filterAuditTable();
    }}

    window.addEventListener('DOMContentLoaded', function() {{
      // Always render immediately from embedded data so charts never appear blank
      initDashboard();
      // Then try to upgrade to live files (only works when served over HTTP, not file://)
      loadLiveData();
    }});
  </script>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Also keep root dashboard.html in sync if possible
    try:
        root_dash = output_path.parent.parent / "dashboard.html"
        with open(root_dash, "w", encoding="utf-8") as f:
            f.write(html_content)
    except Exception:
        pass


if __name__ == "__main__":
    main()
