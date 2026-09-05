"""
src/ask.py
----------
Read-only Q&A assistant over the finished agent output files.

Loads:
    output/audit_log.csv         — per-subscription decision ledger
    output/metrics_summary.json  — aggregate recovery metrics

Builds a concise prompt from that data, then makes ONE direct HTTP call
to an LLM and prints the answer.

Provider selection (env var  ASK_PROVIDER):
    gemini  (default) — Google Gemini via generativelanguage REST API
    claude            — Anthropic Claude via messages REST API

Required env vars:
    GEMINI_API_KEY     when ASK_PROVIDER=gemini  (or unset → gemini)
    ANTHROPIC_API_KEY  when ASK_PROVIDER=claude

Usage:
    python src/ask.py "Which failure reason had the lowest recovery rate?"
    python src/ask.py "How many subscriptions were correctly stopped?"
    ASK_PROVIDER=claude python src/ask.py "What was the net recovery value?"

Constraints enforced by design:
  • Reads ONLY output/audit_log.csv and output/metrics_summary.json.
  • NEVER imports or calls diagnose.py, decide.py, execute.py, or report.py.
  • NEVER writes to any file; makes exactly one API call; then exits.
  • The LLM response is printed to stdout and immediately discarded —
    it has zero influence on any stored record or downstream decision.
"""

import csv
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from textwrap import dedent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
AUDIT_CSV   = BASE_DIR / "output" / "audit_log.csv"
METRICS_JSON = BASE_DIR / "output" / "metrics_summary.json"

# Models used per provider
GEMINI_MODEL = "gemini-1.5-flash"          # fast, cheap, generous free tier
CLAUDE_MODEL = "claude-3-5-haiku-20241022" # fast Haiku — no framework needed


def load_audit() -> list[dict]:
    """Return every row from audit_log.csv as a list of dicts."""
    if not AUDIT_CSV.exists():
        sys.exit(f"ERROR: {AUDIT_CSV} not found. Run the pipeline first.")
    with open(AUDIT_CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_metrics() -> dict:
    """Return the contents of metrics_summary.json."""
    if not METRICS_JSON.exists():
        sys.exit(f"ERROR: {METRICS_JSON} not found. Run the pipeline first.")
    with open(METRICS_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def build_audit_summary(rows: list[dict]) -> str:
    """
    Produce a compact, structured text representation of the audit log.

    Rather than dumping all 75 rows verbatim we:
      1. Show aggregate counts per detected_issue × outcome cross-tab.
      2. Append the full CSV rows (small enough to fit in one prompt).
    """
    # --- Cross-tab: reason × outcome ---
    cross: dict[str, dict[str, int]] = {}
    for r in rows:
        reason  = r.get("detected_issue", "unknown")
        outcome = r.get("outcome", "unknown")
        cross.setdefault(reason, {})
        cross[reason][outcome] = cross[reason].get(outcome, 0) + 1

    lines = ["Cross-tab (detected_issue × outcome):"]
    outcomes_seen = sorted({o for counts in cross.values() for o in counts})
    header = f"  {'Failure Reason':<28}" + "".join(f"  {o:<18}" for o in outcomes_seen)
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for reason, counts in sorted(cross.items()):
        row_str = f"  {reason:<28}" + "".join(
            f"  {counts.get(o, 0):<18}" for o in outcomes_seen
        )
        lines.append(row_str)

    lines.append("")
    lines.append("Full audit log (CSV rows):")
    # Rebuild a compact CSV representation
    if rows:
        cols = list(rows[0].keys())
        lines.append("  " + ",".join(cols))
        for r in rows:
            lines.append("  " + ",".join(r.get(c, "") for c in cols))

    return "\n".join(lines)


def build_prompt(question: str, metrics: dict, audit_summary: str) -> str:
    """Assemble the full prompt sent to the LLM."""
    metrics_block = json.dumps(metrics, indent=2)
    return dedent(f"""\
        You are a read-only analytics assistant for an AI-powered subscription payment
        recovery system. You have access to the following FINISHED output data.
        You must NEVER suggest changing any decision, record, or configuration —
        your role is purely to answer questions about what already happened.

        =======================================================
        METRICS SUMMARY (output/metrics_summary.json)
        =======================================================
        {metrics_block}

        =======================================================
        AUDIT LOG SUMMARY (output/audit_log.csv)
        =======================================================
        {audit_summary}

        =======================================================
        QUESTION
        =======================================================
        {question}

        Answer concisely and factually using only the data above.
        Cite specific numbers where relevant. If the question cannot be
        answered from the available data, say so explicitly.
    """)


# ---------------------------------------------------------------------------
# Provider: Google Gemini  (raw REST, no SDK)
# ---------------------------------------------------------------------------

def call_gemini(prompt: str, api_key: str) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1024,
        },
    }
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        sys.exit(f"Gemini API error {exc.code}: {detail}")

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as exc:
        sys.exit(f"Unexpected Gemini response shape: {exc}\nFull response: {data}")


# ---------------------------------------------------------------------------
# Provider: Anthropic Claude  (raw REST, no SDK)
# ---------------------------------------------------------------------------

def call_claude(prompt: str, api_key: str) -> str:
    url = "https://api.anthropic.com/v1/messages"
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        sys.exit(f"Claude API error {exc.code}: {detail}")

    try:
        return data["content"][0]["text"].strip()
    except (KeyError, IndexError) as exc:
        sys.exit(f"Unexpected Claude response shape: {exc}\nFull response: {data}")


def load_dotenv(dotenv_path: Path) -> None:
    """Lightweight .env loader (pure stdlib, zero dependencies)."""
    if not dotenv_path.is_file():
        return
    try:
        with open(dotenv_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Ensure UTF-8 output when possible on Windows console
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # Auto-load .env from project root if present
    load_dotenv(BASE_DIR / ".env")

    raw_args = sys.argv[1:]
    dry_run = False
    if "--dry-run" in raw_args:
        dry_run = True
        raw_args.remove("--dry-run")

    # --- Parse question from CLI ---
    if not raw_args:
        prog = Path(sys.argv[0]).name
        print(
            f"Usage:  python {prog} [--dry-run] \"<your question>\"\n\n"
            "Examples:\n"
            f'  python {prog} "Which failure reason had the lowest recovery rate?"\n'
            f'  python {prog} "How many subscriptions were correctly stopped?"\n'
            f'  python {prog} --dry-run "What was the net recovery value?"\n\n'
            "Options:\n"
            "  --dry-run         Print the constructed prompt and exit (no API call)\n\n"
            "Environment variables:\n"
            "  ASK_PROVIDER      gemini (default) | claude\n"
            "  GEMINI_API_KEY    required when provider=gemini (or GOOGLE_API_KEY)\n"
            "  ANTHROPIC_API_KEY required when provider=claude\n",
            file=sys.stderr,
        )
        sys.exit(1)

    question = " ".join(raw_args).strip()
    if not question:
        sys.exit("ERROR: Question cannot be empty.")

    # --- Load output files (read-only) ---
    metrics = load_metrics()
    audit_rows = load_audit()

    # --- Build prompt ---
    audit_summary = build_audit_summary(audit_rows)
    prompt = build_prompt(question, metrics, audit_summary)

    # --- Select provider ---
    provider = os.environ.get("ASK_PROVIDER", "gemini").lower().strip()

    if dry_run:
        print("-" * 60)
        print(f"[DRY-RUN] Provider: {provider.title()} | Prompt Length: {len(prompt)} chars")
        print(f"[DRY-RUN] Question: {question}")
        print("-" * 60)
        print("Constructed Prompt:")
        print(prompt)
        print("-" * 60)
        print("[DRY-RUN] Completed without network call.")
        return

    if provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
        if not api_key:
            sys.exit(
                "ERROR: GEMINI_API_KEY (or GOOGLE_API_KEY) environment variable is not set.\n"
                "  Set it, or use ASK_PROVIDER=claude with ANTHROPIC_API_KEY.\n"
                "  Tip: You can use --dry-run to inspect the prompt without an API key."
            )
        print(f"[ask.py] Provider: Gemini ({GEMINI_MODEL})", file=sys.stderr)
        answer = call_gemini(prompt, api_key)

    elif provider == "claude":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            sys.exit(
                "ERROR: ANTHROPIC_API_KEY environment variable is not set.\n"
                "  Set it, or use ASK_PROVIDER=gemini with GEMINI_API_KEY.\n"
                "  Tip: You can use --dry-run to inspect the prompt without an API key."
            )
        print(f"[ask.py] Provider: Claude ({CLAUDE_MODEL})", file=sys.stderr)
        answer = call_claude(prompt, api_key)

    else:
        sys.exit(
            f"ERROR: Unknown provider '{provider}'. "
            "Set ASK_PROVIDER to 'gemini' or 'claude'."
        )

    # --- Print answer to stdout (read-only; nothing written to disk) ---
    print()
    print("-" * 60)
    print(f"Q: {question}")
    print("-" * 60)
    print(answer)
    print("-" * 60)


if __name__ == "__main__":
    main()
