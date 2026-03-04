#!/usr/bin/env python3
import argparse
import json
import os
import sys

import pandas as pd
from edgar import Company, configure_http, set_identity

# Initialize Edgar HTTP settings exactly once before any API calls.
configure_http(use_system_certs=True)

if "EDGAR_IDENTITY" in os.environ:
    set_identity(os.environ["EDGAR_IDENTITY"])
else:
    print("WARNING: EDGAR_IDENTITY environment variable is not set. SEC requires a valid User-Agent.", file=sys.stderr)
    set_identity("OpenClaw_Agent <bot@openclaw.ai>")

pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 2000)


def _emit_json(payload):
    print(json.dumps(payload, ensure_ascii=False, default=str))


def _error_hint(err):
    msg = str(err)
    low = msg.lower()
    if "ssl" in low or "tls" in low or "unexpected_eof_while_reading" in low:
        return "Network/TLS handshake failed. Check proxy settings and direct access to https://data.sec.gov."
    if "edgar_identity" in low or "user-agent" in low:
        return "Set EDGAR_IDENTITY, e.g. export EDGAR_IDENTITY='Your Name <you@example.com>'."
    if "not found" in low and "concept" in low:
        return "Concept not found for this ticker. Use search-concepts first to find the exact concept id."
    if "not in index" in low:
        return "Returned schema differs from expected columns. Try latest code and inspect available columns."
    return "Check ticker/form/concept arguments and retry."


def _print_error(prefix, err, as_json=False):
    if as_json:
        _emit_json({"ok": False, "error": str(err), "hint": _error_hint(err)})
    else:
        print(f"{prefix}: {err}")
        print(f"Hint: {_error_hint(err)}")


def _render_statement(financials_obj, attr_name, label):
    print(f"\n--- {label} ---")
    try:
        stmt = getattr(financials_obj, attr_name, None)
        if stmt is None:
            print(f"  [{label} not available]")
            return
        if callable(stmt):
            stmt = stmt()
        if stmt is None:
            print(f"  [{label} not available]")
            return
        if hasattr(stmt, 'to_dataframe') and callable(stmt.to_dataframe):
            print(stmt.to_dataframe().to_string())
        elif isinstance(stmt, pd.DataFrame):
            print(stmt.to_string())
        else:
            print(str(stmt))
    except Exception as e:
        print(f"  [Error rendering {label.lower()}: {e}]")


def get_financials(args):
    c = Company(args.ticker)
    filings = c.get_filings(form=args.form)
    if len(filings) == 0:
        msg = f"No {args.form} filings found for {args.ticker}."
        if args.json:
            _emit_json({"ok": False, "error": msg})
        else:
            print(msg)
        return

    rows = []
    for f in filings.head(args.limit):
        try:
            obj = f.obj()
            if args.json:
                row = {
                    "form": str(f.form),
                    "filing_date": str(f.filing_date),
                    "report_date": str(getattr(f, 'report_date', None)),
                    "accession_no": str(getattr(f, 'accession_no', None)),
                    "statements": {}
                }
                if hasattr(obj, 'financials') and obj.financials:
                    for key in ["income_statement", "balance_sheet", "cash_flow_statement"]:
                        stmt = getattr(obj.financials, key, None)
                        if callable(stmt):
                            stmt = stmt()
                        if stmt is not None and hasattr(stmt, 'to_dataframe') and callable(stmt.to_dataframe):
                            row["statements"][key] = stmt.to_dataframe().to_dict(orient="records")
                        else:
                            row["statements"][key] = None
                rows.append(row)
            else:
                print(f"\n{'='*80}\nFiling: {f.form} filed on {f.filing_date} (Period ending: {f.report_date})")
                if hasattr(obj, 'financials') and obj.financials:
                    if args.statement in ['income', 'all']:
                        _render_statement(obj.financials, 'income_statement', 'Income Statement')
                    if args.statement in ['balance', 'all']:
                        _render_statement(obj.financials, 'balance_sheet', 'Balance Sheet')
                    if args.statement in ['cash', 'all']:
                        _render_statement(obj.financials, 'cash_flow_statement', 'Cash Flow Statement')
                else:
                    print("Financials object not found or empty for this filing.")
        except Exception as e:
            _print_error(f"Error parsing financials for accession {getattr(f, 'accession_no', 'unknown')}", e, as_json=args.json)
            return

    if args.json:
        _emit_json({"ok": True, "ticker": args.ticker, "form": args.form, "rows": rows})

def search_concepts(args):
    c = Company(args.ticker)
    facts = c.get_facts()
    if not facts:
        print("No facts found for this company.")
        return
    df = facts.to_dataframe()
    # matches concept or label
    matches = df[df['concept'].str.contains(args.keyword, case=False, na=False) | df['label'].str.contains(args.keyword, case=False, na=False)]
    if matches.empty:
        print(f"No XBRL concepts found matching '{args.keyword}' for {args.ticker}.")
    else:
        unique_facts = matches[['concept', 'label']].drop_duplicates()
        print(f"Found {len(unique_facts)} concepts matching '{args.keyword}':")
        for _, row in unique_facts.iterrows():
            print(f" - {row['concept']} (Label: {row['label']})")

def get_facts(args):
    try:
        c = Company(args.ticker)
        facts = c.get_facts()
        if not facts:
            if args.json:
                _emit_json({"ok": False, "error": "No facts found."})
            else:
                print("No facts found.")
            return

        df = facts.to_dataframe()
        concept_df = df[df['concept'] == args.concept].copy()
        if concept_df.empty:
            msg = f"Concept '{args.concept}' not found for {args.ticker}. Try using 'search-concepts' first."
            if args.json:
                _emit_json({"ok": False, "error": msg})
            else:
                print(msg)
            return

        # Normalize period_end and derive canonical year from period_end (source of truth)
        concept_df['period_end'] = pd.to_datetime(concept_df['period_end'], errors='coerce')
        concept_df = concept_df.dropna(subset=['period_end'])
        concept_df['derived_year'] = concept_df['period_end'].dt.year

        # Keep raw fiscal metadata for auditability
        if 'fiscal_year' in concept_df.columns:
            concept_df['fiscal_year_raw'] = pd.to_numeric(concept_df['fiscal_year'], errors='coerce')
            concept_df['year_mismatch'] = concept_df['fiscal_year_raw'].notna() & (concept_df['fiscal_year_raw'] != concept_df['derived_year'])
        else:
            concept_df['fiscal_year_raw'] = pd.NA
            concept_df['year_mismatch'] = False

        # Annual preference for 10-K style requests: prefer FY rows when available; otherwise fall back.
        if args.form == '10-K' and 'fiscal_period' in concept_df.columns:
            fy_df = concept_df[concept_df['fiscal_period'] == 'FY']
            if not fy_df.empty:
                concept_df = fy_df

        # Deduplicate by period_end after all filters, keep the latest row for each date.
        concept_df = concept_df.sort_values(by=['period_end']).drop_duplicates(subset=['period_end'], keep='last')
        concept_df = concept_df.sort_values(by='period_end', ascending=False)
        concept_df = concept_df.head(args.limit)

        concept_df['period_end'] = concept_df['period_end'].dt.date.astype(str)
        display_cols = ['period_end', 'derived_year', 'fiscal_year_raw', 'fiscal_period', 'numeric_value', 'unit', 'year_mismatch']
        display_cols = [c for c in display_cols if c in concept_df.columns]

        if args.json:
            _emit_json({
                "ok": True,
                "ticker": args.ticker,
                "concept": args.concept,
                "form": args.form,
                "rows": concept_df[display_cols].to_dict(orient='records')
            })
        else:
            print(f"\nTime Series for concept '{args.concept}' ({args.ticker}):")
            print(concept_df[display_cols].to_string(index=False))
    except Exception as e:
        _print_error("Error fetching facts", e, as_json=args.json)

def list_filings(args):
    c = Company(args.ticker)
    filings = c.get_filings(form=args.form) if args.form else c.get_filings()
    if len(filings) == 0:
        print(f"No filings found for {args.ticker}.")
        return

    df = filings.head(args.limit).to_pandas()

    # Compatibility across edgartools versions.
    if 'report_date' not in df.columns and 'reportDate' in df.columns:
        df['report_date'] = df['reportDate']
    if 'accession_no' not in df.columns and 'accession_number' in df.columns:
        df['accession_no'] = df['accession_number']

    desired_cols = ['form', 'filing_date', 'report_date', 'accession_no']
    available_cols = [c for c in desired_cols if c in df.columns]

    print(f"\nRecent Filings for {args.ticker}:")
    if available_cols:
        print(df[available_cols].to_string(index=False))
    else:
        print(df.to_string(index=False))

def get_eight_k(args):
    c = Company(args.ticker)
    filings = c.get_filings(form="8-K")
    if len(filings) == 0:
        print(f"No 8-K filings found for {args.ticker}.")
        return
    for f in filings.head(args.limit):
        print(f"\n{'='*80}\n8-K Filed on {f.filing_date} (Accession: {f.accession_no})")
        try:
            obj = f.obj()
            items = getattr(obj, 'items', 'N/A')
            print(f"Reported Items: {items}")
            text = ""
            if hasattr(obj, 'text') and callable(obj.text): text = obj.text()
            elif hasattr(f, 'text') and callable(f.text): text = f.text()
            else: text = str(obj)
            print(f"\n--- Content Snippet (First 2000 chars) ---\n{text[:2000]}...\n")
        except Exception as e: print(f"Error parsing 8-K: {e}")

def get_insider(args):
    c = Company(args.ticker)
    filings = c.get_filings(form="4")
    if len(filings) == 0:
        print(f"No Form 4 filings found for {args.ticker}.")
        return
    for f in filings.head(args.limit):
        print(f"\n{'='*80}\nForm 4 Filed on {f.filing_date} (Accession: {f.accession_no})")
        try: print(str(f.obj()))
        except Exception as e: print(f"Error parsing Form 4: {e}")

def search_text(args):
    c = Company(args.ticker)
    filings = c.get_filings(form=args.form)
    if len(filings) == 0:
        print(f"No {args.form} filings found for {args.ticker}.")
        return
    for f in filings.head(args.limit):
        print(f"\n{'='*80}\nSearching in {f.form} filed on {f.filing_date}")
        try:
            if hasattr(f, 'text') and callable(f.text): text = f.text()
            else:
                obj = f.obj()
                if hasattr(obj, 'text') and callable(obj.text): text = obj.text()
                else: text = str(obj)
            keyword = args.keyword.lower()
            text_lower = text.lower()
            start = 0
            matches = 0
            window = args.window
            while True:
                idx = text_lower.find(keyword, start)
                if idx == -1: break
                matches += 1
                if matches > args.max_matches:
                    print(f"\n  [Reached max matches limit of {args.max_matches}.]")
                    break
                win_start = max(0, idx - window)
                win_end = min(len(text), idx + len(keyword) + window)
                snippet = " ".join(text[win_start:win_end].split())
                print(f"\n--- Match {matches} ---\n...{snippet}...")
                start = idx + len(keyword)
            if matches == 0: print(f"Keyword '{args.keyword}' not found.")
        except Exception as e: print(f"Error searching text: {e}")

def read_item(args):
    c = Company(args.ticker)
    filings = c.get_filings(form=args.form).head(1)
    if len(filings) == 0:
        print(f"No {args.form} filings found for {args.ticker}.")
        return
    f = filings[0]
    print(f"\n{'='*80}\nReading {args.item} from {f.form} filed on {f.filing_date}")
    try:
        obj = f.obj()
        if hasattr(obj, '__getitem__'):
            try:
                text = obj[args.item]
                if not text:
                    print(f"[{args.item} is empty]")
                    return
                if len(text) > args.max_chars:
                    print(f"(Truncating to first {args.max_chars} chars...)\n\n{text[:args.max_chars]}\n\n...[TRUNCATED]")
                else: print(text)
            except KeyError: print(f"Item '{args.item}' not found. Available: {getattr(obj, 'items', 'Unknown')}")
        else: print(f"Object does not support item extraction.")
    except Exception as e: print(f"Error reading item: {e}")


def self_test(args):
    checks = []

    def run_check(name, fn):
        try:
            fn()
            checks.append({"name": name, "ok": True})
        except Exception as e:
            checks.append({"name": name, "ok": False, "error": str(e), "hint": _error_hint(e)})

    run_check("identity", lambda: (_ for _ in ()).throw(RuntimeError("EDGAR_IDENTITY is missing")) if "EDGAR_IDENTITY" not in os.environ else None)

    def _check_list_filings():
        c = Company(args.ticker)
        f = c.get_filings(form="10-K")
        if len(f) == 0:
            raise RuntimeError(f"No 10-K filings found for {args.ticker}")

    def _check_facts():
        c = Company(args.ticker)
        facts = c.get_facts()
        if not facts:
            raise RuntimeError(f"No facts available for {args.ticker}")

    run_check("list-filings-connectivity", _check_list_filings)
    run_check("facts-connectivity", _check_facts)

    ok = all(c.get("ok") for c in checks)
    if args.json:
        _emit_json({"ok": ok, "ticker": args.ticker, "checks": checks})
    else:
        print(f"Self-test for {args.ticker}: {'PASS' if ok else 'FAIL'}")
        for c in checks:
            if c["ok"]:
                print(f"  [PASS] {c['name']}")
            else:
                print(f"  [FAIL] {c['name']}: {c.get('error')}")
                print(f"         hint: {c.get('hint')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    p = subparsers.add_parser("financials")
    p.add_argument("--ticker", required=True)
    p.add_argument("--form", default="10-K")
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--statement", default="all")
    p.set_defaults(func=get_financials)
    
    p = subparsers.add_parser("search-concepts")
    p.add_argument("--ticker", required=True)
    p.add_argument("--keyword", required=True)
    p.set_defaults(func=search_concepts)
    
    p = subparsers.add_parser("facts")
    p.add_argument("--ticker", required=True)
    p.add_argument("--concept", required=True)
    p.add_argument("--form", default="10-K")
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=get_facts)

    p = subparsers.add_parser("list-filings")
    p.add_argument("--ticker", required=True)
    p.add_argument("--form", default="")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=list_filings)

    p = subparsers.add_parser("eight-k")
    p.add_argument("--ticker", required=True)
    p.add_argument("--limit", type=int, default=3)
    p.set_defaults(func=get_eight_k)

    p = subparsers.add_parser("insider")
    p.add_argument("--ticker", required=True)
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=get_insider)

    p = subparsers.add_parser("search-text")
    p.add_argument("--ticker", required=True)
    p.add_argument("--keyword", required=True)
    p.add_argument("--form", default="10-K")
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--window", type=int, default=500)
    p.add_argument("--max_matches", type=int, default=10)
    p.set_defaults(func=search_text)

    p = subparsers.add_parser("read-item")
    p.add_argument("--ticker", required=True)
    p.add_argument("--form", default="10-K")
    p.add_argument("--item", required=True)
    p.add_argument("--max_chars", type=int, default=4000)
    p.set_defaults(func=read_item)

    p = subparsers.add_parser("self-test")
    p.add_argument("--ticker", default="AAPL")
    p.set_defaults(func=self_test)
    
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as e:
        _print_error("Unhandled error", e, as_json=getattr(args, 'json', False))
        sys.exit(1)

if __name__ == "__main__":
    main()
