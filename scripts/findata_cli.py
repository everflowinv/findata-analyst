#!/usr/bin/env python3
import argparse
import sys
import os
import pandas as pd
from edgar import Company, set_identity

pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 2000)

if "EDGAR_IDENTITY" in os.environ:
    set_identity(os.environ["EDGAR_IDENTITY"])
else:
    print("WARNING: EDGAR_IDENTITY environment variable is not set. SEC requires a valid User-Agent.", file=sys.stderr)
    set_identity("OpenClaw_Agent <bot@openclaw.ai>")

def get_financials(args):
    c = Company(args.ticker)
    filings = c.get_filings(form=args.form)
    if len(filings) == 0:
        print(f"No {args.form} filings found for {args.ticker}.")
        return
    for f in filings.head(args.limit):
        print(f"\n{'='*80}\nFiling: {f.form} filed on {f.filing_date} (Period ending: {f.report_date})")
        try:
            obj = f.obj()
            if hasattr(obj, 'financials') and obj.financials:
                if args.statement in ['income', 'all']:
                    print("\n--- Income Statement ---")
                    try: print(obj.financials.income_statement.to_dataframe().to_string())
                    except Exception as e: print(f"  [Error rendering income statement: {e}]")
                if args.statement in ['balance', 'all']:
                    print("\n--- Balance Sheet ---")
                    try: print(obj.financials.balance_sheet.to_dataframe().to_string())
                    except Exception as e: print(f"  [Error rendering balance sheet: {e}]")
                if args.statement in ['cash', 'all']:
                    print("\n--- Cash Flow Statement ---")
                    try: print(obj.financials.cash_flow_statement.to_dataframe().to_string())
                    except Exception as e: print(f"  [Error rendering cash flow: {e}]")
            else:
                print("Financials object not found or empty for this filing.")
        except Exception as e:
            print(f"Error parsing financials for accession {f.accession_no}: {e}")

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
    c = Company(args.ticker)
    facts = c.get_facts()
    if not facts:
        print("No facts found.")
        return
    df = facts.to_dataframe()
    concept_df = df[df['concept'] == args.concept]
    if concept_df.empty:
        print(f"Concept '{args.concept}' not found for {args.ticker}. Try using 'search-concepts' first.")
        return
    
    # In edgartools EntityFacts, period_type is useful (instant or duration)
    # fiscal_year and fiscal_period help determine Q1/Q2/Q3/FY
    # form isn't directly in this dataframe usually, but let's see
    
    # Let's drop duplicates by period_end to get a clean timeline
    concept_df = concept_df.sort_values(by=['period_end']).drop_duplicates(subset=['period_end'], keep='last')
    concept_df = concept_df.sort_values(by='period_end', ascending=False)
    
    # Filter for annual if form 10-K requested (roughly 'FY' or period_type 'duration' with ~365 days)
    if args.form == '10-K':
        concept_df = concept_df[concept_df['fiscal_period'] == 'FY']
        
    concept_df = concept_df.head(args.limit)
    
    print(f"\nTime Series for concept '{args.concept}' ({args.ticker}):")
    display_cols = ['period_end', 'fiscal_year', 'fiscal_period', 'numeric_value', 'unit']
    print(concept_df[display_cols].to_string(index=False))

def list_filings(args):
    c = Company(args.ticker)
    filings = c.get_filings(form=args.form) if args.form else c.get_filings()
    if len(filings) == 0:
        print(f"No filings found for {args.ticker}.")
        return
    df = filings.head(args.limit).to_pandas()
    print(f"\nRecent Filings for {args.ticker}:")
    print(df[['form', 'filing_date', 'report_date', 'accession_no']].to_string(index=False))

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

def main():
    parser = argparse.ArgumentParser()
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
    
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
