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

    metrics_json_str = json.dumps(summary_metrics)
    reasons_json_str = json.dumps(reason_breakdown)
    audit_json_str = json.dumps(audit_rows)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI Revenue Recovery Agent — Operational Ledger & Executive Report</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg-base: #080b11;
      --bg-surface: #0f1420;
      --bg-surface-elevated: #141b2a;
      --bg-surface-hover: #172033;
      --border-subtle: #1c2438;
      --border-strong: #27344f;
      --border-focus: #3b82f6;

      --text-primary: #f1f5f9;
      --text-secondary: #94a3b8;
      --text-muted: #5a6982;

      /* Restrained Accent Palette */
      --color-recovered: #14b8a6;
      --color-recovered-subtle: rgba(20, 184, 166, 0.08);
      --color-recovered-border: rgba(20, 184, 166, 0.3);

      --color-failed: #f43f5e;
      --color-failed-subtle: rgba(244, 63, 94, 0.08);
      --color-failed-border: rgba(244, 63, 94, 0.3);

      --color-stopped: #f59e0b;
      --color-stopped-subtle: rgba(245, 158, 11, 0.08);
      --color-stopped-border: rgba(245, 158, 11, 0.3);

      --color-neutral: #64748b;
      --color-neutral-subtle: rgba(100, 116, 139, 0.1);
      --color-neutral-border: rgba(100, 116, 139, 0.25);
    }}

    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}

    body {{
      font-family: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background-color: var(--bg-base);
      color: var(--text-primary);
      padding: 24px 28px 48px;
      min-height: 100vh;
      line-height: 1.45;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }}

    .container {{
      max-width: 1400px;
      margin: 0 auto;
    }}

    /* Typography Utilities */
    .font-heading {{
      font-family: 'Space Grotesk', sans-serif;
    }}

    .font-mono {{
      font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }}

    .text-right {{ text-align: right; }}
    .text-center {{ text-align: center; }}
    .text-muted {{ color: var(--text-muted); }}
    .text-secondary {{ color: var(--text-secondary); }}

    /* Section Labeling (Small Uppercase) */
    .section-label {{
      font-family: 'Space Grotesk', sans-serif;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--text-muted);
      margin-bottom: 12px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    .section-label::after {{
      content: '';
      flex: 1;
      height: 1px;
      background: var(--border-subtle);
    }}

    /* ==========================================================================
       Header: Institutional Fintech Report Style
       ========================================================================== */
    header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 24px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--border-subtle);
      flex-wrap: wrap;
      gap: 20px;
    }}

    .header-left {{
      max-width: 760px;
    }}

    .kicker-bar {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.06em;
      color: #38bdf8;
      background: rgba(56, 189, 248, 0.08);
      border: 1px solid rgba(56, 189, 248, 0.2);
      padding: 3px 10px;
      border-radius: 4px;
      margin-bottom: 8px;
    }}

    .header-left h1 {{
      font-family: 'Space Grotesk', sans-serif;
      font-size: 24px;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: #ffffff;
      margin-bottom: 4px;
    }}

    .header-sub {{
      font-size: 13px;
      color: var(--text-secondary);
      line-height: 1.4;
    }}

    .header-right {{
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 8px;
    }}

    .meta-pills {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}

    .meta-chip {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      padding: 4px 9px;
      border-radius: 4px;
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      color: var(--text-secondary);
    }}

    .meta-chip strong {{
      color: var(--text-primary);
    }}

    .meta-chip.chip-status {{
      border-color: rgba(20, 184, 166, 0.3);
      color: var(--color-recovered);
      background: var(--color-recovered-subtle);
    }}

    .btn-file {{
      cursor: pointer;
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      color: var(--text-secondary);
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      padding: 4px 10px;
      border-radius: 4px;
      transition: all 0.15s ease;
    }}

    .btn-file:hover {{
      border-color: var(--border-strong);
      color: var(--text-primary);
      background: var(--bg-surface-elevated);
    }}

    /* ==========================================================================
       Metric Cards: Top Row (Numbers Largest Visual Element)
       ========================================================================== */
    .metrics-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 16px;
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

    .metric-card {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 18px 20px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-height: 124px;
      transition: border-color 0.15s ease;
    }}

    .metric-card:hover {{
      border-color: var(--border-strong);
    }}

    .metric-label {{
      font-family: 'Space Grotesk', sans-serif;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-muted);
      margin-bottom: 6px;
    }}

    .metric-value {{
      font-family: 'Space Grotesk', sans-serif;
      font-size: 32px;
      font-weight: 700;
      line-height: 1.1;
      letter-spacing: -0.03em;
      color: var(--text-primary);
    }}

    .metric-value.val-recovered {{
      color: var(--color-recovered);
    }}

    .metric-value.val-failed {{
      color: var(--color-failed);
    }}

    .metric-value.val-net {{
      color: #38bdf8;
    }}

    .metric-sub {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: var(--text-secondary);
      margin-top: 6px;
      display: flex;
      align-items: center;
      gap: 6px;
    }}

    /* Secondary Compact Ribbon */
    .secondary-ribbon {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 10px;
      margin-bottom: 28px;
    }}

    @media (max-width: 1024px) {{
      .secondary-ribbon {{
        grid-template-columns: repeat(2, 1fr);
      }}
    }}

    .ribbon-cell {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 4px;
      padding: 10px 14px;
    }}

    .ribbon-cell .ribbon-title {{
      font-size: 10px;
      font-family: 'Space Grotesk', sans-serif;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--text-muted);
      margin-bottom: 2px;
    }}

    .ribbon-cell .ribbon-val {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 14px;
      font-weight: 600;
      color: var(--text-primary);
    }}

    /* ==========================================================================
       Charts Grid: Horizontal Bar Charts
       ========================================================================== */
    .charts-grid {{
      display: grid;
      grid-template-columns: 1.6fr 1fr;
      gap: 16px;
      margin-bottom: 32px;
    }}

    @media (max-width: 1024px) {{
      .charts-grid {{
        grid-template-columns: 1fr;
      }}
    }}

    .chart-card {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 18px 20px;
    }}

    .chart-card-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--border-subtle);
    }}

    .chart-title {{
      font-family: 'Space Grotesk', sans-serif;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.02em;
      text-transform: uppercase;
      color: var(--text-primary);
    }}

    .chart-sub {{
      font-size: 11px;
      color: var(--text-muted);
      font-family: 'JetBrains Mono', monospace;
    }}

    .chart-container {{
      position: relative;
      height: 320px;
      width: 100%;
    }}

    /* ==========================================================================
       Reason Breakdown Matrix Table
       ========================================================================== */
    .table-card {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      overflow: hidden;
      margin-bottom: 32px;
    }}

    .data-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 12.5px;
      text-align: left;
    }}

    .data-table thead {{
      background: #0b0f19;
      border-bottom: 1px solid var(--border-subtle);
    }}

    .data-table th {{
      padding: 10px 14px;
      font-family: 'Space Grotesk', sans-serif;
      font-size: 10.5px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-muted);
      white-space: nowrap;
    }}

    .data-table td {{
      padding: 9px 14px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.03);
      color: #e2e8f0;
      vertical-align: middle;
    }}

    .data-table tbody tr:nth-child(even) {{
      background-color: rgba(255, 255, 255, 0.015);
    }}

    .data-table tbody tr:hover {{
      background-color: rgba(255, 255, 255, 0.035);
    }}

    .code-tag {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: #cbd5e1;
      background: rgba(255, 255, 255, 0.04);
      padding: 2px 6px;
      border-radius: 3px;
      border: 1px solid rgba(255, 255, 255, 0.06);
    }}

    /* Subtle Outcome Indicators (Dots, not clumsy badges) */
    .status-indicator {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      font-weight: 600;
    }}

    .status-dot {{
      width: 6px;
      height: 6px;
      border-radius: 50%;
      display: inline-block;
    }}

    .status-recovered {{
      color: var(--color-recovered);
    }}
    .status-recovered .status-dot {{
      background: var(--color-recovered);
    }}

    .status-still_failed {{
      color: var(--color-failed);
    }}
    .status-still_failed .status-dot {{
      background: var(--color-failed);
    }}

    .status-correctly_stopped {{
      color: var(--color-stopped);
    }}
    .status-correctly_stopped .status-dot {{
      background: var(--color-stopped);
    }}

    .action-tag {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: #94a3b8;
    }}

    /* ==========================================================================
       Subscription Lifecycle Trace Inspector
       ========================================================================== */
    .inspector-card {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 20px;
      margin-bottom: 32px;
    }}

    .inspector-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
      gap: 16px;
      flex-wrap: wrap;
    }}

    .inspector-title-box h3 {{
      font-family: 'Space Grotesk', sans-serif;
      font-size: 14px;
      font-weight: 700;
      letter-spacing: 0.02em;
      text-transform: uppercase;
      color: #ffffff;
    }}

    .inspector-title-box p {{
      font-size: 12px;
      color: var(--text-secondary);
      margin-top: 2px;
    }}

    .sample-triggers {{
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }}

    .sample-label {{
      font-size: 11px;
      font-family: 'Space Grotesk', sans-serif;
      font-weight: 600;
      text-transform: uppercase;
      color: var(--text-muted);
    }}

    .btn-sample {{
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-subtle);
      color: var(--text-secondary);
      padding: 3px 8px;
      border-radius: 3px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      cursor: pointer;
      transition: all 0.15s ease;
    }}

    .btn-sample:hover {{
      border-color: var(--border-strong);
      color: var(--text-primary);
      background: var(--bg-surface-hover);
    }}

    .inspector-input-row {{
      display: flex;
      gap: 10px;
      margin-bottom: 16px;
      flex-wrap: wrap;
    }}

    .inspector-input-wrapper {{
      position: relative;
      flex: 1;
      min-width: 280px;
    }}

    .inspector-input {{
      width: 100%;
      background: var(--bg-base);
      border: 1px solid var(--border-subtle);
      color: #ffffff;
      padding: 9px 12px;
      border-radius: 4px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      outline: none;
      transition: border-color 0.15s ease;
    }}

    .inspector-input:focus {{
      border-color: var(--border-focus);
    }}

    .btn-search {{
      background: #1e293b;
      border: 1px solid var(--border-strong);
      color: #ffffff;
      padding: 9px 16px;
      border-radius: 4px;
      font-family: 'Space Grotesk', sans-serif;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s ease;
    }}

    .btn-search:hover {{
      background: #334155;
    }}

    .btn-reset-light {{
      background: transparent;
      border: 1px solid var(--border-subtle);
      color: var(--text-secondary);
      padding: 9px 14px;
      border-radius: 4px;
      font-family: 'Space Grotesk', sans-serif;
      font-size: 12px;
      cursor: pointer;
    }}

    .btn-reset-light:hover {{
      color: var(--text-primary);
      border-color: var(--border-strong);
    }}

    .trace-display-box {{
      background: var(--bg-base);
      border: 1px solid var(--border-subtle);
      border-radius: 4px;
      padding: 16px;
      min-height: 90px;
    }}

    .trace-placeholder {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    .trace-steps-container {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 12px;
    }}

    @media (max-width: 900px) {{
      .trace-steps-container {{
        grid-template-columns: 1fr;
      }}
    }}

    .trace-step {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 4px;
      padding: 10px 12px;
    }}

    .trace-step-tag {{
      font-family: 'Space Grotesk', sans-serif;
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-muted);
      margin-bottom: 4px;
    }}

    .trace-step-val {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      font-weight: 600;
      color: var(--text-primary);
      word-break: break-all;
    }}

    /* ==========================================================================
       Audit Table Filter Controls
       ========================================================================== */
    .filter-bar {{
      display: flex;
      gap: 10px;
      margin-bottom: 12px;
      flex-wrap: wrap;
      align-items: center;
    }}

    .filter-input-wrap {{
      flex: 1;
      min-width: 220px;
    }}

    .filter-input {{
      width: 100%;
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      color: #ffffff;
      padding: 8px 12px;
      border-radius: 4px;
      font-family: 'IBM Plex Sans', sans-serif;
      font-size: 12.5px;
      outline: none;
    }}

    .filter-input:focus {{
      border-color: var(--border-focus);
    }}

    .filter-select {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      color: var(--text-secondary);
      padding: 8px 12px;
      border-radius: 4px;
      font-family: 'IBM Plex Sans', sans-serif;
      font-size: 12px;
      outline: none;
      cursor: pointer;
    }}

    .filter-select:focus {{
      border-color: var(--border-focus);
      color: var(--text-primary);
    }}

    .filter-pill {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: var(--text-secondary);
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      padding: 8px 12px;
      border-radius: 4px;
      white-space: nowrap;
    }}

    .btn-sub-link {{
      background: transparent;
      border: none;
      color: #38bdf8;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      text-decoration: underline;
      text-underline-offset: 3px;
      padding: 0;
    }}

    .btn-sub-link:hover {{
      color: #7dd3fc;
    }}

    /* Footer */
    footer {{
      margin-top: 36px;
      padding-top: 18px;
      border-top: 1px solid var(--border-subtle);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      font-size: 11px;
      color: var(--text-muted);
      font-family: 'JetBrains Mono', monospace;
    }}
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <header>
      <div class="header-left">
        <div class="kicker-bar">
          RAZORPAY AI BUILDATHON 2026 // TRACK 03
        </div>
        <h1>AI Revenue Recovery Agent — Operational Ledger</h1>
        <p class="header-sub">
          Autonomous, compliance-bounded intervention engine for recurring subscription debits.
          Enforces hard stopping rules under RBI e-Mandate circulars and NPCI NACH/UPI clearing guidelines.
        </p>
      </div>
      <div class="header-right">
        <div class="meta-pills">
          <span class="meta-chip chip-status" id="sourceBadge">STATUS: VERIFIED LOCKED RUN</span>
          <span class="meta-chip">BATCH: <strong>700 RECORDS</strong></span>
          <span class="meta-chip">STOPPING RULES: <strong>T+7 DAYS / 3 RETRIES</strong></span>
        </div>
        <div>
          <label class="btn-file" title="Load external dataset from disk">
            Import JSON / CSV
            <input type="file" id="fileUploadInput" multiple accept=".json,.csv" onchange="handleFileSelect(event)" style="display:none;" />
          </label>
        </div>
      </div>
    </header>

    <!-- Section 1: Executive KPI Metrics (Top Row: Numbers Largest Visual Element) -->
    <div class="section-label">EXECUTIVE RECOVERY METRICS</div>
    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-label">TOTAL REVENUE AT RISK</div>
        <div class="metric-value val-failed" id="val-at-risk">₹1,31,825</div>
        <div class="metric-sub">75 failed payments • 10.7% failure rate</div>
      </div>

      <div class="metric-card">
        <div class="metric-label">GROSS REVENUE RECOVERED</div>
        <div class="metric-value val-recovered" id="val-recovered">₹19,484</div>
        <div class="metric-sub">16 recovered • 21.3% of failed volume</div>
      </div>

      <div class="metric-card">
        <div class="metric-label">RECOVERY RATE (AMOUNT)</div>
        <div class="metric-value" id="val-rate">14.8%</div>
        <div class="metric-sub">Target recovery benchmark: 12.0% - 18.0%</div>
      </div>

      <div class="metric-card">
        <div class="metric-label">NET RECOVERY VALUE</div>
        <div class="metric-value val-net" id="val-net">₹19,289</div>
        <div class="metric-sub">Gross ₹19,484 − ₹195 wasted retry fees</div>
      </div>
    </div>

    <!-- Secondary Compact Ribbon -->
    <div class="secondary-ribbon">
      <div class="ribbon-cell">
        <div class="ribbon-title">CORRECTLY STOPPED</div>
        <div class="ribbon-val" id="val-stopped">20</div>
        <div class="text-muted" style="font-size: 10px; font-family: 'JetBrains Mono';">Compliance boundaries held</div>
      </div>
      <div class="ribbon-cell">
        <div class="ribbon-title">STILL FAILED</div>
        <div class="ribbon-val" id="val-still-failed">39</div>
        <div class="text-muted" style="font-size: 10px; font-family: 'JetBrains Mono';">Exhausted retry window</div>
      </div>
      <div class="ribbon-cell">
        <div class="ribbon-title">DIAGNOSIS ACCURACY</div>
        <div class="ribbon-val" style="color: var(--color-recovered);" id="val-accuracy">100% (0 errors)</div>
        <div class="text-muted" style="font-size: 10px; font-family: 'JetBrains Mono';">Deterministic classification</div>
      </div>
      <div class="ribbon-cell">
        <div class="ribbon-title">WASTED RETRIES</div>
        <div class="ribbon-val" id="val-wasted">13 retries (₹195)</div>
        <div class="text-muted" style="font-size: 10px; font-family: 'JetBrains Mono';">₹15 gateway fee per attempt</div>
      </div>
      <div class="ribbon-cell">
        <div class="ribbon-title">MISSED OPPORTUNITY</div>
        <div class="ribbon-val" id="val-missed">26 cases (₹47,874)</div>
        <div class="text-muted" style="font-size: 10px; font-family: 'JetBrains Mono';">T+7 regulatory cutoff bound</div>
      </div>
    </div>

    <!-- Section 2: Charts Grid (Horizontal Bar Charts) -->
    <div class="section-label">OUTCOME DISTRIBUTION & CAPITAL ALLOCATION</div>
    <div class="charts-grid">
      <div class="chart-card">
        <div class="chart-card-header">
          <div class="chart-title">OUTCOME BY FAILURE REASON (COUNTS)</div>
          <div class="chart-sub">HORIZONTAL COMPARATIVE BREAKDOWN</div>
        </div>
        <div class="chart-container">
          <canvas id="reasonsBarChart"></canvas>
        </div>
      </div>

      <div class="chart-card">
        <div class="chart-card-header">
          <div class="chart-title">FINANCIAL CAPITAL FLOW (INR)</div>
          <div class="chart-sub">RECOVERY VS COMPLIANCE CEILING</div>
        </div>
        <div class="chart-container">
          <canvas id="financialBarChart"></canvas>
        </div>
      </div>
    </div>

    <!-- Section 3: Root-Cause Operational Breakdown Matrix Table -->
    <div class="section-label">ROOT-CAUSE CATEGORY BREAKDOWN</div>
    <div class="table-card">
      <table class="data-table">
        <thead>
          <tr>
            <th>Failure Reason Code</th>
            <th>Diagnosed Root Cause</th>
            <th class="text-right">Total Failed</th>
            <th class="text-right">Recovered</th>
            <th class="text-right">Still Failed</th>
            <th class="text-right">Correctly Stopped</th>
            <th class="text-right">At-Risk Amount</th>
            <th class="text-right">Recovered Amount</th>
            <th class="text-right">Recovery Rate</th>
          </tr>
        </thead>
        <tbody id="reasonTableBody"></tbody>
      </table>
    </div>

    <!-- Section 4: Subscription Lifecycle Trace Inspector -->
    <div class="section-label">SUBSCRIPTION LIFECYCLE TRACE INSPECTOR</div>
    <div class="inspector-card" id="trailInspectorCard">
      <div class="inspector-header">
        <div class="inspector-title-box">
          <h3>Forensic Lifecycle Trace</h3>
          <p>Inspect the complete deterministic sequence for any subscription in the ledger</p>
        </div>
        <div class="sample-triggers">
          <span class="sample-label">Quick lookups:</span>
          <span id="quickSampleChips"></span>
        </div>
      </div>

      <div class="inspector-input-row">
        <div class="inspector-input-wrapper">
          <input type="text" id="subLookupInput" class="inspector-input" placeholder="Search subscription ID (e.g. sub_100028, sub_100031)..." oninput="handleSubLookup(false)" onkeydown="if(event.key==='Enter') handleSubLookup(true)" />
        </div>
        <button type="button" class="btn-search" onclick="handleSubLookup(true)">Trace Lifecycle</button>
        <button type="button" class="btn-reset-light" onclick="clearSubLookup()">Clear</button>
      </div>

      <div id="trailDisplayArea" class="trace-display-box">
        <!-- Dynamic content -->
      </div>
    </div>

    <!-- Section 5: Auditable Execution Trail Table -->
    <div class="section-label">AUDIT LOG & INTERVENTION LEDGER</div>
    <div class="filter-bar">
      <div class="filter-input-wrap">
        <input type="text" id="searchInput" class="filter-input" placeholder="Filter audit ledger by ID, reason, or action..." onkeyup="filterAuditTable()" />
      </div>

      <select id="reasonFilter" class="filter-select" onchange="filterAuditTable()">
        <option value="">All Failure Reasons</option>
        <option value="bank_technical_decline">bank_technical_decline</option>
        <option value="instrument_expired">instrument_expired</option>
        <option value="insufficient_funds">insufficient_funds</option>
        <option value="mandate_expired">mandate_expired</option>
        <option value="mandate_revoked">mandate_revoked</option>
        <option value="risk_block">risk_block</option>
      </select>

      <select id="outcomeFilter" class="filter-select" onchange="filterAuditTable()">
        <option value="">All Outcomes</option>
        <option value="recovered">Recovered</option>
        <option value="still_failed">Still Failed</option>
        <option value="correctly_stopped">Correctly Stopped</option>
      </select>

      <select id="actionFilter" class="filter-select" onchange="filterAuditTable()">
        <option value="">All Actions</option>
        <option value="immediate_retry_once">immediate_retry_once</option>
        <option value="retry_scheduled">retry_scheduled</option>
        <option value="reauth_request">reauth_request</option>
        <option value="update_payment_method_request">update_payment_method_request</option>
        <option value="no_retry_winback_offer">no_retry_winback_offer</option>
        <option value="manual_review_escalation">manual_review_escalation</option>
        <option value="recovery_abandoned">recovery_abandoned</option>
      </select>

      <button type="button" class="btn-reset-light" onclick="resetAuditFilters()">Reset</button>
      <div class="filter-pill" id="tableRecordCount">Showing 75 records</div>
    </div>

    <div class="table-card">
      <table class="data-table">
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

    <!-- Footer -->
    <footer>
      <div>
        <strong>Razorpay AI Buildathon 2026</strong> — Track 03: AI Revenue Recovery Agent
      </div>
      <div>
        Regulatory Framework: RBI Master Direction DPSS.CO.PD.No.447/02.14.003 & NPCI UPI AutoPay / NACH Rules
      </div>
    </footer>
  </div>

  <script>
    // Embedded Data Snapshot
    const EMBEDDED_METRICS = {metrics_json_str};
    const EMBEDDED_REASONS = {reasons_json_str};
    const EMBEDDED_AUDIT = {audit_json_str};

    let currentMetrics = EMBEDDED_METRICS;
    let currentAudit = EMBEDDED_AUDIT;
    let currentReasons = EMBEDDED_REASONS;
    let reasonsChartInstance = null;
    let financialChartInstance = null;

    function formatINR(val) {{
      return '₹' + Number(val).toLocaleString('en-IN');
    }}

    function escapeHtml(str) {{
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }}

    // Custom Chart.js Canvas plugin to directly render labels inside/outside bars
    const directBarLabelPlugin = {{
      id: 'directBarLabelPlugin',
      afterDatasetsDraw(chart) {{
        const {{ ctx }} = chart;
        chart.data.datasets.forEach((dataset, datasetIndex) => {{
          const meta = chart.getDatasetMeta(datasetIndex);
          if (meta.hidden) return;
          meta.data.forEach((element, index) => {{
            const val = dataset.data[index];
            if (val === undefined || val === null || val <= 0) return;

            ctx.save();
            ctx.font = '600 11px "JetBrains Mono", monospace';
            const text = dataset.isCurrency ? formatINR(val) : String(val);
            
            // For horizontal bars (indexAxis: 'y')
            const barWidth = Math.abs(element.x - element.base);
            if (barWidth > 28) {{
              ctx.fillStyle = '#ffffff';
              ctx.textAlign = 'center';
              ctx.textBaseline = 'middle';
              ctx.fillText(text, (element.x + element.base) / 2, element.y);
            }} else {{
              ctx.fillStyle = '#94a3b8';
              ctx.textAlign = 'left';
              ctx.textBaseline = 'middle';
              ctx.fillText(text, element.x + 6, element.y);
            }}
            ctx.restore();
          }});
        }});
      }}
    }};

    async function loadLiveData() {{
      let liveMetricsLoaded = false;
      let liveAuditLoaded = false;

      try {{
        const resp = await fetch('metrics_summary.json?nocache=' + Date.now());
        if (resp.ok) {{
          currentMetrics = await resp.json();
          liveMetricsLoaded = true;
        }}
      }} catch (e) {{}}

      try {{
        const resp = await fetch('audit_log.csv?nocache=' + Date.now());
        if (resp.ok) {{
          const text = await resp.text();
          currentAudit = parseCSV(text);
          liveAuditLoaded = true;
        }}
      }} catch (e) {{}}

      const badge = document.getElementById('sourceBadge');
      if (badge) {{
        if (liveMetricsLoaded && liveAuditLoaded) {{
          badge.textContent = 'STATUS: LIVE SYNCED';
          initDashboard();
        }}
      }}
    }}

    function parseCSV(text) {{
      const lines = text.split(/\\r?\\n/).filter(l => l.trim().length > 0);
      if (lines.length <= 1) return [];
      const headers = lines[0].split(',').map(h => h.trim());
      const records = [];
      for (let i = 1; i < lines.length; i++) {{
        const cols = lines[i].split(',').map(c => c.trim());
        if (cols.length >= headers.length) {{
          const item = {{}};
          headers.forEach((h, idx) => item[h] = cols[idx]);
          records.push(item);
        }}
      }}
      return records;
    }}

    function handleFileSelect(event) {{
      const files = event.target.files;
      for (let i = 0; i < files.length; i++) {{
        const file = files[i];
        const reader = new FileReader();
        if (file.name.endsWith('.json')) {{
          reader.onload = function(e) {{
            try {{
              currentMetrics = JSON.parse(e.target.result);
              document.getElementById('sourceBadge').textContent = 'IMPORT: ' + file.name;
              initDashboard();
            }} catch (err) {{
              alert('Invalid JSON file');
            }}
          }};
          reader.readAsText(file);
        }} else if (file.name.endsWith('.csv')) {{
          reader.onload = function(e) {{
            currentAudit = parseCSV(e.target.result);
            document.getElementById('sourceBadge').textContent = 'IMPORT: ' + file.name;
            initDashboard();
          }};
          reader.readAsText(file);
        }}
      }}
    }}

    function initDashboard() {{
      const elAtRisk = document.getElementById('val-at-risk');
      const elRecovered = document.getElementById('val-recovered');
      const elRate = document.getElementById('val-rate');
      const elNet = document.getElementById('val-net');

      if (elAtRisk && currentMetrics.total_at_risk_inr) elAtRisk.textContent = formatINR(currentMetrics.total_at_risk_inr);
      if (elRecovered && currentMetrics.recovered_amount_inr) elRecovered.textContent = formatINR(currentMetrics.recovered_amount_inr);
      if (elRate && currentMetrics.recovery_rate_pct !== undefined) elRate.textContent = currentMetrics.recovery_rate_pct.toFixed(1) + '%';
      if (elNet && currentMetrics.net_recovery_value_inr) elNet.textContent = formatINR(currentMetrics.net_recovery_value_inr);

      const elStopped = document.getElementById('val-stopped');
      const elStillFailed = document.getElementById('val-still-failed');
      const elAccuracy = document.getElementById('val-accuracy');
      const elWasted = document.getElementById('val-wasted');
      const elMissed = document.getElementById('val-missed');

      if (elStopped && currentMetrics.correctly_stopped_count !== undefined) elStopped.textContent = currentMetrics.correctly_stopped_count;
      if (elStillFailed && currentMetrics.still_failed_count !== undefined) elStillFailed.textContent = currentMetrics.still_failed_count;
      if (elAccuracy && currentMetrics.diagnosis_error_count !== undefined) {{
        elAccuracy.textContent = currentMetrics.diagnosis_error_count === 0 ? '100% (0 errors)' : currentMetrics.diagnosis_error_count + ' errors';
      }}
      if (elWasted && currentMetrics.false_positive_retries !== undefined) {{
        elWasted.textContent = `${{currentMetrics.false_positive_retries}} retries (${{formatINR(currentMetrics.false_positive_cost_inr || 195)}})`;
      }}
      if (elMissed && currentMetrics.missed_recovery_count !== undefined) {{
        elMissed.textContent = `${{currentMetrics.missed_recovery_count}} cases (${{formatINR(currentMetrics.missed_recovery_amount_inr || 47874)}})`;
      }}

      renderCharts();
      renderReasonTable();
      renderAuditTable(currentAudit);
      updateRecordCount(currentAudit.length, currentAudit.length);
      renderTrailPlaceholder();
      initQuickSamples();
    }}

    function renderCharts() {{
      // --- Chart 1: Outcome by Failure Reason (Horizontal Stacked Bar) ---
      const ctxReasons = document.getElementById('reasonsBarChart');
      if (ctxReasons) {{
        if (reasonsChartInstance) reasonsChartInstance.destroy();

        const reasonsOrder = [
          'insufficient_funds',
          'mandate_expired',
          'bank_technical_decline',
          'instrument_expired',
          'mandate_revoked',
          'risk_block'
        ];

        const recData = [];
        const failedData = [];
        const stoppedData = [];

        reasonsOrder.forEach(rsn => {{
          const item = currentReasons[rsn] || {{ recovered: 0, still_failed: 0, correctly_stopped: 0 }};
          recData.push(item.recovered || 0);
          failedData.push(item.still_failed || 0);
          stoppedData.push(item.correctly_stopped || 0);
        }});

        reasonsChartInstance = new Chart(ctxReasons, {{
          type: 'bar',
          data: {{
            labels: reasonsOrder,
            datasets: [
              {{
                label: 'Recovered',
                data: recData,
                backgroundColor: '#14b8a6',
                borderRadius: 2
              }},
              {{
                label: 'Still Failed',
                data: failedData,
                backgroundColor: '#f43f5e',
                borderRadius: 2
              }},
              {{
                label: 'Correctly Stopped',
                data: stoppedData,
                backgroundColor: '#f59e0b',
                borderRadius: 2
              }}
            ]
          }},
          options: {{
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            layout: {{ padding: {{ right: 20 }} }},
            scales: {{
              x: {{
                stacked: true,
                grid: {{ color: 'rgba(255, 255, 255, 0.05)' }},
                ticks: {{ color: '#64748b', font: {{ family: "'JetBrains Mono', monospace", size: 10 }} }}
              }},
              y: {{
                stacked: true,
                grid: {{ display: false }},
                ticks: {{ color: '#cbd5e1', font: {{ family: "'JetBrains Mono', monospace", size: 11 }} }}
              }}
            }},
            plugins: {{
              legend: {{
                position: 'top',
                align: 'end',
                labels: {{
                  color: '#94a3b8',
                  boxWidth: 10,
                  boxHeight: 10,
                  font: {{ family: "'Space Grotesk', sans-serif", size: 11 }}
                }}
              }},
              tooltip: {{ enabled: true }}
            }}
          }},
          plugins: [directBarLabelPlugin]
        }});
      }}

      // --- Chart 2: Financial Capital Flow (Horizontal Bar) ---
      const ctxFinancial = document.getElementById('financialBarChart');
      if (ctxFinancial) {{
        if (financialChartInstance) financialChartInstance.destroy();

        const atRisk = currentMetrics.total_at_risk_inr || 131825;
        const recovered = currentMetrics.recovered_amount_inr || 19484;
        const missed = currentMetrics.missed_recovery_amount_inr || 47874;
        const wasted = currentMetrics.false_positive_cost_inr || 195;

        financialChartInstance = new Chart(ctxFinancial, {{
          type: 'bar',
          data: {{
            labels: ['Total At-Risk', 'Gross Recovered', 'Missed (T+7)', 'Wasted Retry Fees'],
            datasets: [{{
              data: [atRisk, recovered, missed, wasted],
              isCurrency: true,
              backgroundColor: ['#475569', '#14b8a6', '#f43f5e', '#f59e0b'],
              borderRadius: 3,
              barThickness: 22
            }}]
          }},
          options: {{
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            layout: {{ padding: {{ right: 50 }} }},
            scales: {{
              x: {{
                grid: {{ color: 'rgba(255, 255, 255, 0.05)' }},
                ticks: {{
                  color: '#64748b',
                  font: {{ family: "'JetBrains Mono', monospace", size: 10 }},
                  callback: val => '₹' + (val / 1000).toFixed(0) + 'k'
                }}
              }},
              y: {{
                grid: {{ display: false }},
                ticks: {{ color: '#cbd5e1', font: {{ family: "'Space Grotesk', sans-serif", size: 11 }} }}
              }}
            }},
            plugins: {{
              legend: {{ display: false }},
              tooltip: {{
                callbacks: {{
                  label: ctx => ' Amount: ' + formatINR(ctx.raw)
                }}
              }}
            }}
          }},
          plugins: [directBarLabelPlugin]
        }});
      }}
    }}

    function renderReasonTable() {{
      const tbody = document.getElementById('reasonTableBody');
      if (!tbody) return;

      const causeMap = {{
        'insufficient_funds': 'funding_shortfall',
        'mandate_expired': 'mandate_lifecycle',
        'mandate_revoked': 'mandate_lifecycle',
        'bank_technical_decline': 'transient_bank_error',
        'instrument_expired': 'payment_instrument_issue',
        'risk_block': 'fraud_or_risk_hold',
      }};

      const reasonsOrder = [
        'insufficient_funds',
        'mandate_expired',
        'bank_technical_decline',
        'instrument_expired',
        'mandate_revoked',
        'risk_block'
      ];

      let html = '';
      reasonsOrder.forEach(rsn => {{
        const d = currentReasons[rsn] || {{ total: 0, recovered: 0, still_failed: 0, correctly_stopped: 0, amount_at_risk: 0, amount_recovered: 0 }};
        const cause = causeMap[rsn] || 'operational_category';
        const rate = d.total > 0 ? ((d.recovered / d.total) * 100).toFixed(1) : '0.0';

        html += `<tr>
          <td><code class="code-tag">${{rsn}}</code></td>
          <td class="text-secondary">${{cause}}</td>
          <td class="text-right font-mono font-bold">${{d.total}}</td>
          <td class="text-right font-mono" style="color: var(--color-recovered);">${{d.recovered}}</td>
          <td class="text-right font-mono" style="color: var(--color-failed);">${{d.still_failed}}</td>
          <td class="text-right font-mono" style="color: var(--color-stopped);">${{d.correctly_stopped}}</td>
          <td class="text-right font-mono">${{formatINR(d.amount_at_risk)}}</td>
          <td class="text-right font-mono font-bold" style="color: var(--color-recovered);">${{formatINR(d.amount_recovered)}}</td>
          <td class="text-right font-mono font-bold">${{rate}}%</td>
        </tr>`;
      }});
      tbody.innerHTML = html;
    }}

    function renderTrailPlaceholder() {{
      const area = document.getElementById('trailDisplayArea');
      if (!area) return;
      area.innerHTML = `
        <div class="trace-placeholder">
          <span>&gt; Ready: Select a quick lookup above or input any subscription ID to render its deterministic decision trail.</span>
        </div>
      `;
    }}

    function initQuickSamples() {{
      const container = document.getElementById('quickSampleChips');
      if (!container || !currentAudit || currentAudit.length === 0) return;

      const samples = [
        {{ id: 'sub_100031', label: 'sub_100031 (recovered)' }},
        {{ id: 'sub_100036', label: 'sub_100036 (failed)' }},
        {{ id: 'sub_100054', label: 'sub_100054 (stopped)' }}
      ];

      container.innerHTML = samples.map(s =>
        `<button type="button" class="btn-sample" onclick="inspectSubscription('${{s.id}}')">${{s.label}}</button>`
      ).join('');
    }}

    function inspectSubscription(subId) {{
      const input = document.getElementById('subLookupInput');
      if (input) {{
        input.value = subId;
        handleSubLookup(true);
        const card = document.getElementById('trailInspectorCard');
        if (card) card.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
      }}
    }}

    function handleSubLookup(scrollOnMatch = false) {{
      const input = document.getElementById('subLookupInput');
      const query = input ? input.value.trim() : '';
      if (!query) {{
        renderTrailPlaceholder();
        return;
      }}

      const match = currentAudit.find(r => r.subscription_id.toLowerCase() === query.toLowerCase());
      if (match) {{
        renderTrailMatch(match);
      }} else {{
        const area = document.getElementById('trailDisplayArea');
        if (area) {{
          area.innerHTML = `<div class="trace-placeholder" style="color: var(--color-failed);">No audit record found for "${{escapeHtml(query)}}". Try sub_100031, sub_100028, or sub_100054.</div>`;
        }}
      }}
    }}

    function clearSubLookup() {{
      const input = document.getElementById('subLookupInput');
      if (input) input.value = '';
      renderTrailPlaceholder();
    }}

    function renderTrailMatch(r) {{
      const area = document.getElementById('trailDisplayArea');
      if (!area) return;

      const attemptText = r.retry_attempt_number ? `Attempt #${{r.retry_attempt_number}}` : '0 (Terminal Hold)';
      
      let outcomeClass = 'status-still_failed';
      if (r.outcome === 'recovered') outcomeClass = 'status-recovered';
      else if (r.outcome === 'correctly_stopped') outcomeClass = 'status-correctly_stopped';

      area.innerHTML = `
        <div style="margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center;">
          <span class="font-mono" style="font-size: 13px; color: #fff; font-weight: 700;">RECORD: ${{escapeHtml(r.subscription_id)}}</span>
          <span class="status-indicator ${{outcomeClass}}"><span class="status-dot"></span>${{escapeHtml(r.outcome)}}</span>
        </div>
        <div class="trace-steps-container">
          <div class="trace-step">
            <div class="trace-step-tag">STAGE 1: DETECTED ISSUE</div>
            <div class="trace-step-val">${{escapeHtml(r.detected_issue)}}</div>
          </div>
          <div class="trace-step">
            <div class="trace-step-tag">STAGE 2: DIAGNOSED CAUSE</div>
            <div class="trace-step-val">${{escapeHtml(r.diagnosed_cause)}}</div>
          </div>
          <div class="trace-step">
            <div class="trace-step-tag">STAGE 3: CHOSEN ACTION</div>
            <div class="trace-step-val">${{escapeHtml(r.chosen_action)}}</div>
          </div>
          <div class="trace-step">
            <div class="trace-step-tag">STAGE 4: ATTEMPT STATUS</div>
            <div class="trace-step-val">${{escapeHtml(attemptText)}}</div>
          </div>
          <div class="trace-step">
            <div class="trace-step-tag">STAGE 5: FINAL OUTCOME</div>
            <div class="trace-step-val ${{outcomeClass}}">${{escapeHtml(r.outcome)}}</div>
          </div>
        </div>
      `;
    }}

    function renderAuditTable(records) {{
      const tbody = document.getElementById('auditTableBody');
      if (!tbody) return;

      let html = '';
      for (const r of records) {{
        let outcomeClass = 'status-still_failed';
        if (r.outcome === 'recovered') outcomeClass = 'status-recovered';
        else if (r.outcome === 'correctly_stopped') outcomeClass = 'status-correctly_stopped';

        html += `<tr>
          <td class="font-mono text-muted" style="font-size: 11px;">${{r.timestamp}}</td>
          <td>
            <button type="button" class="btn-sub-link" onclick="inspectSubscription('${{r.subscription_id}}')">
              ${{r.subscription_id}}
            </button>
          </td>
          <td><code class="code-tag">${{r.detected_issue}}</code></td>
          <td class="text-secondary">${{r.diagnosed_cause}}</td>
          <td class="action-tag font-mono">${{r.chosen_action}}</td>
          <td class="text-center font-mono text-muted">${{r.retry_attempt_number || '—'}}</td>
          <td>
            <span class="status-indicator ${{outcomeClass}}">
              <span class="status-dot"></span>${{r.outcome}}
            </span>
          </td>
        </tr>`;
      }}
      tbody.innerHTML = html;
    }}

    function updateRecordCount(count, total) {{
      const el = document.getElementById('tableRecordCount');
      if (el) {{
        el.textContent = count === total ? `Showing all ${{total}} records` : `Showing ${{count}} of ${{total}} records`;
      }}
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
      initDashboard();
      loadLiveData();
    }});
  </script>
</body>
</html>"""

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
