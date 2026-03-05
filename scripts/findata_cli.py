#!/usr/bin/env python3
"""findata-analyst CLI v2 — SEC EDGAR data with NL routing, caching, and structured output."""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Edgar setup
# ---------------------------------------------------------------------------
from edgar import Company, configure_http, set_identity

configure_http(use_system_certs=True)

if "EDGAR_IDENTITY" in os.environ:
    set_identity(os.environ["EDGAR_IDENTITY"])
else:
    print("WARNING: EDGAR_IDENTITY not set. SEC requires a valid User-Agent.", file=sys.stderr)
    set_identity("OpenClaw_Agent <bot@openclaw.ai>")

pd.set_option("display.max_rows", None)
pd.set_option("display.max_columns", None)
pd.set_option("display.width", 2000)

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
CACHE_MAX_AGE_S = 86400  # 24h


def _emit(payload, as_json=False, as_csv=False):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, default=str))
    elif as_csv and isinstance(payload, dict) and "rows" in payload:
        rows = payload["rows"]
        if rows:
            cols = list(rows[0].keys())
            print(",".join(cols))
            for r in rows:
                vals = [str(r.get(c, "")).replace('"', '""') for c in cols]
                print(",".join(f'"{v}"' if "," in v else v for v in vals))
    else:
        print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))


def _error_hint(err):
    msg = str(err).lower()
    if "ssl" in msg or "tls" in msg or "unexpected_eof" in msg:
        return "Network/TLS error. Check proxy and access to https://data.sec.gov."
    if "edgar_identity" in msg or "user-agent" in msg:
        return "Set EDGAR_IDENTITY: export EDGAR_IDENTITY='Name <email>'."
    if "not found" in msg and "concept" in msg:
        return "Concept not found. Use search-concepts or smart-facts to find exact concept id."
    if "rate" in msg or "429" in msg or "too many" in msg:
        return "SEC rate limit hit. Wait 10s and retry."
    return "Check ticker/form/concept arguments and retry."


def _error_json(err):
    return {"ok": False, "error": str(err), "hint": _error_hint(err)}


# ---------------------------------------------------------------------------
# Retry mechanism
# ---------------------------------------------------------------------------
def _retry(fn, max_retries=2, delay=2.0):
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            is_transient = any(k in err_str for k in [
                "timeout", "timed out", "connection", "reset", "429",
                "rate", "ssl", "eof", "too many",
            ])
            if not is_transient or attempt >= max_retries:
                raise
            time.sleep(delay * (attempt + 1))
    raise last_err


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def _cache_key(prefix, *args):
    raw = f"{prefix}:{'|'.join(str(a) for a in args)}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _cache_get(key):
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        age = time.time() - path.stat().st_mtime
        if age < CACHE_MAX_AGE_S:
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
    return None


def _cache_set(key, data):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(CACHE_DIR / f"{key}.json", "w") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# NL Router
# ---------------------------------------------------------------------------
_NL_PATTERNS = [
    # 8-K / events
    (r"(8-K|8K|recent event|corporate event|earnings release|material event)", "eight-k"),
    # Insider / Form 4
    (r"(insider|form\s*4|executive buy|executive sell|insider trad|管理层买卖|高管交易)", "insider"),
    # Ratios
    (r"(gross margin|net margin|operating margin|profit margin|roe|roa|毛利率|净利率|利润率)", "ratios"),
    # Compare
    (r"(compare|vs\.?|versus|对比|哪个.*高|哪个.*好|两家)", "compare"),
    # Financial statements
    (r"(income statement|balance sheet|cash flow|financial statement|报表|利润表|资产负债)", "financials"),
    # Business model / qualitative
    (r"(business model|商业模式|what does.*do|Item\s*1\b)", "read-item"),
    # Depreciation / specific policy search
    (r"(depreciation|useful life|accounting polic|lease term|折旧|会计政策)", "search-text"),
    # XBRL concept search
    (r"(segment|revenue by|breakdown|xbrl|concept)", "smart-facts"),
    # Facts / historical metrics
    (r"(historical|trend|过去.*年|time series|growth|增速|cagr)", "smart-facts"),
    # Default: smart-facts for metric questions
    (r"(revenue|earnings|net income|gross profit|ebitda|capex|收入|利润)", "smart-facts"),
]


def _route_nl(question: str) -> tuple[str, dict]:
    """Return (command, extra_kwargs) based on NL question."""
    q_lower = question.lower()

    # Extract ticker(s)
    tickers = re.findall(r"\b([A-Z]{1,5})\b", question)
    # Filter out common English words
    stop_words = {"THE", "AND", "FOR", "ARE", "NOT", "BUT", "HAS", "HAD", "WAS", "IS", "IT", "OR", "AN", "DO", "IF",
                  "VS", "A", "IN", "ON", "AT", "TO", "BY", "OF", "UP", "NO", "SO", "AS", "BE", "AM", "HOW", "WHY",
                  "GPM", "NPM", "ROE", "ROA", "EPS", "PE", "PB", "YOY", "CAGR", "MD", "SEC", "XBRL", "IPO",
                  "WHAT", "WHEN", "WHERE", "WHO", "WHICH", "ITEM", "FROM"}
    tickers = [t for t in tickers if t not in stop_words and len(t) >= 2]

    for pattern, cmd in _NL_PATTERNS:
        if re.search(pattern, q_lower if pattern.startswith("(") and any(c.islower() for c in pattern) else question, re.IGNORECASE):
            return cmd, {"tickers": tickers, "question": question}

    # Fallback: if we have tickers, try smart-facts
    if tickers:
        return "smart-facts", {"tickers": tickers, "question": question}

    return "unknown", {"tickers": tickers, "question": question}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_financials(args):
    c = _retry(lambda: Company(args.ticker))
    filings = _retry(lambda: c.get_filings(form=args.form))
    if len(filings) == 0:
        return {"ok": False, "error": f"No {args.form} filings found for {args.ticker}."}

    rows = []
    for f in filings.head(args.limit):
        try:
            obj = f.obj()
            row = {
                "form": str(f.form), "filing_date": str(f.filing_date),
                "report_date": str(getattr(f, "report_date", None)),
                "accession_no": str(getattr(f, "accession_no", None)),
                "statements": {},
            }
            if hasattr(obj, "financials") and obj.financials:
                stmts = {"income": "income_statement", "balance": "balance_sheet", "cash": "cash_flow_statement"}
                for key, attr in stmts.items():
                    if args.statement not in (key, "all"):
                        continue
                    stmt = getattr(obj.financials, attr, None)
                    if callable(stmt):
                        stmt = stmt()
                    if stmt is not None and hasattr(stmt, "to_dataframe"):
                        row["statements"][key] = stmt.to_dataframe().to_dict(orient="records")
                    else:
                        row["statements"][key] = None
            rows.append(row)
        except Exception as e:
            rows.append({"form": str(f.form), "filing_date": str(f.filing_date), "error": str(e)})
            continue  # ← FIXED: was return
    return {"ok": True, "ticker": args.ticker, "form": args.form, "rows": rows}


def cmd_search_concepts(args):
    ck = _cache_key("concepts", args.ticker, args.keyword)
    cached = _cache_get(ck)
    if cached:
        return cached

    c = _retry(lambda: Company(args.ticker))
    facts = _retry(lambda: c.get_facts())
    if not facts:
        return {"ok": False, "error": f"No facts found for {args.ticker}."}

    df = facts.to_dataframe()
    matches = df[
        df["concept"].str.contains(args.keyword, case=False, na=False)
        | df["label"].str.contains(args.keyword, case=False, na=False)
    ]
    if matches.empty:
        return {"ok": False, "error": f"No concepts matching '{args.keyword}' for {args.ticker}."}

    unique = matches[["concept", "label"]].drop_duplicates().to_dict(orient="records")
    result = {"ok": True, "ticker": args.ticker, "keyword": args.keyword, "count": len(unique), "concepts": unique}
    _cache_set(ck, result)
    return result


def cmd_facts(args):
    ck = _cache_key("facts", args.ticker, args.concept, args.form, args.limit)
    cached = _cache_get(ck)
    if cached:
        return cached

    c = _retry(lambda: Company(args.ticker))
    facts = _retry(lambda: c.get_facts())
    if not facts:
        return {"ok": False, "error": "No facts found."}

    df = facts.to_dataframe()
    concept_df = df[df["concept"] == args.concept].copy()
    if concept_df.empty:
        return {"ok": False, "error": f"Concept '{args.concept}' not found. Use search-concepts first."}

    concept_df["period_end"] = pd.to_datetime(concept_df["period_end"], errors="coerce")
    concept_df = concept_df.dropna(subset=["period_end"])
    concept_df["derived_year"] = concept_df["period_end"].dt.year

    if "fiscal_year" in concept_df.columns:
        concept_df["fiscal_year_raw"] = pd.to_numeric(concept_df["fiscal_year"], errors="coerce")
        concept_df["year_mismatch"] = concept_df["fiscal_year_raw"].notna() & (concept_df["fiscal_year_raw"] != concept_df["derived_year"])
    else:
        concept_df["fiscal_year_raw"] = pd.NA
        concept_df["year_mismatch"] = False

    if args.form == "10-K" and "fiscal_period" in concept_df.columns:
        fy_df = concept_df[concept_df["fiscal_period"] == "FY"]
        if not fy_df.empty:
            concept_df = fy_df

    concept_df = concept_df.sort_values("period_end").drop_duplicates(subset=["period_end"], keep="last")
    concept_df = concept_df.sort_values("period_end", ascending=False).head(args.limit)
    concept_df["period_end"] = concept_df["period_end"].dt.date.astype(str)

    display_cols = ["period_end", "derived_year", "fiscal_year_raw", "fiscal_period", "numeric_value", "unit", "year_mismatch"]
    display_cols = [c for c in display_cols if c in concept_df.columns]

    result = {"ok": True, "ticker": args.ticker, "concept": args.concept, "form": args.form,
              "rows": concept_df[display_cols].to_dict(orient="records")}
    _cache_set(ck, result)
    return result


def cmd_smart_facts(args):
    """Auto concept search + fetch: one-step from keyword to time series."""
    keyword = getattr(args, "keyword", None) or getattr(args, "question", "") or ""
    if not keyword:
        return {"ok": False, "error": "Need --keyword or a question to search concepts."}

    # Step 1: search concepts
    c = _retry(lambda: Company(args.ticker))
    facts = _retry(lambda: c.get_facts())
    if not facts:
        return {"ok": False, "error": f"No facts found for {args.ticker}."}

    df = facts.to_dataframe()
    matches = df[
        df["concept"].str.contains(keyword, case=False, na=False)
        | df["label"].str.contains(keyword, case=False, na=False)
    ]
    if matches.empty:
        return {"ok": False, "error": f"No concepts matching '{keyword}' for {args.ticker}.",
                "hint": "Try broader keywords (e.g., 'revenue' instead of 'total revenue')."}

    unique = matches[["concept", "label"]].drop_duplicates()

    # Step 2: pick best concept (prefer us-gaap, non-deprecated, most data)
    best_concept = None
    best_score = -1
    for _, row in unique.head(15).iterrows():
        concept = row["concept"]
        label = str(row.get("label", ""))
        count = len(df[df["concept"] == concept])
        score = count
        # Prefer us-gaap concepts
        if concept.startswith("us-gaap:"):
            score += 100
        # Penalize deprecated concepts
        if "deprecated" in label.lower():
            score -= 200
        # Prefer concepts whose label closely matches the keyword
        if keyword.lower() in label.lower():
            score += 50
        if score > best_score:
            best_score = score
            best_concept = concept

    if not best_concept:
        return {"ok": False, "error": "Could not determine best concept."}

    # Step 3: fetch time series for best concept
    concept_df = df[df["concept"] == best_concept].copy()
    concept_df["period_end"] = pd.to_datetime(concept_df["period_end"], errors="coerce")
    concept_df = concept_df.dropna(subset=["period_end"])
    concept_df["derived_year"] = concept_df["period_end"].dt.year

    if "fiscal_year" in concept_df.columns:
        concept_df["fiscal_year_raw"] = pd.to_numeric(concept_df["fiscal_year"], errors="coerce")
        concept_df["year_mismatch"] = concept_df["fiscal_year_raw"].notna() & (concept_df["fiscal_year_raw"] != concept_df["derived_year"])
    else:
        concept_df["fiscal_year_raw"] = pd.NA
        concept_df["year_mismatch"] = False

    if args.form == "10-K" and "fiscal_period" in concept_df.columns:
        fy_df = concept_df[concept_df["fiscal_period"] == "FY"]
        if not fy_df.empty:
            concept_df = fy_df

    concept_df = concept_df.sort_values("period_end").drop_duplicates(subset=["period_end"], keep="last")
    concept_df = concept_df.sort_values("period_end", ascending=False).head(args.limit)
    concept_df["period_end"] = concept_df["period_end"].dt.date.astype(str)

    display_cols = ["period_end", "derived_year", "fiscal_year_raw", "fiscal_period", "numeric_value", "unit", "year_mismatch"]
    display_cols = [c for c in display_cols if c in concept_df.columns]

    return {
        "ok": True, "ticker": args.ticker, "keyword": keyword,
        "chosen_concept": best_concept,
        "all_concepts": unique.head(5).to_dict(orient="records"),
        "form": args.form,
        "rows": concept_df[display_cols].to_dict(orient="records"),
    }


def cmd_compare(args):
    """Compare a metric across two tickers."""
    tickers = args.tickers.split(",") if hasattr(args, "tickers") and args.tickers else []
    if len(tickers) < 2:
        return {"ok": False, "error": "Need two tickers separated by comma (e.g., --tickers NVDA,AVGO)."}

    keyword = args.keyword
    results = {}
    for ticker in tickers[:2]:
        ticker = ticker.strip().upper()
        try:
            # Reuse smart-facts logic
            ns = argparse.Namespace(ticker=ticker, keyword=keyword, form=args.form, limit=args.limit, question="")
            r = cmd_smart_facts(ns)
            results[ticker] = r
        except Exception as e:
            results[ticker] = {"ok": False, "error": str(e)}

    return {"ok": True, "mode": "compare", "keyword": keyword, "form": args.form, "results": results}


def cmd_ratios(args):
    """Calculate common financial ratios from latest filing."""
    c = _retry(lambda: Company(args.ticker))
    filings = _retry(lambda: c.get_filings(form=args.form))
    if len(filings) == 0:
        return {"ok": False, "error": f"No {args.form} filings found for {args.ticker}."}

    ratios_list = []
    for f in filings.head(args.limit):
        try:
            obj = f.obj()
            if not hasattr(obj, "financials") or not obj.financials:
                continue

            income = getattr(obj.financials, "income_statement", None)
            if callable(income):
                income = income()
            balance = getattr(obj.financials, "balance_sheet", None)
            if callable(balance):
                balance = balance()

            def _get_val(stmt, *names):
                """Get latest period value by matching label (row-based, edgartools format)."""
                if stmt is None:
                    return None
                df_stmt = stmt.to_dataframe() if hasattr(stmt, "to_dataframe") else stmt
                if not isinstance(df_stmt, pd.DataFrame) or df_stmt.empty:
                    return None
                # Find the latest date column (format: YYYY-MM-DD)
                date_cols = [c for c in df_stmt.columns if re.match(r"\d{4}-\d{2}-\d{2}", str(c))]
                if not date_cols:
                    return None
                latest_col = sorted(date_cols, reverse=True)[0]
                # Match by label column (row-based)
                label_col = "label" if "label" in df_stmt.columns else None
                if not label_col:
                    return None
                # Filter out dimension breakdowns (keep consolidated only)
                if "dimension" in df_stmt.columns:
                    consolidated = df_stmt[df_stmt["dimension"] == False]
                    if not consolidated.empty:
                        df_stmt = consolidated
                for name in names:
                    mask = df_stmt[label_col].str.contains(name, case=False, na=False)
                    matches = df_stmt[mask]
                    # Filter out rows with "liabilities" when searching for equity
                    if "equity" in name.lower() or "shareholders" in name.lower() or "stockholders" in name.lower():
                        exclude = matches[label_col].str.contains("liabilities", case=False, na=False)
                        matches = matches[~exclude]
                    if not matches.empty:
                        val = matches.iloc[0][latest_col]
                        if pd.notna(val):
                            return float(val)
                return None

            revenue = _get_val(income, "net sales", "revenue", "total revenue", "net revenue", "sales")
            gross_profit = _get_val(income, "gross margin", "gross profit")
            operating_income = _get_val(income, "operating income", "operating profit", "income from operations")
            net_income = _get_val(income, "net income")
            total_assets = _get_val(balance, "total assets")
            total_equity = _get_val(balance, "total shareholders", "total stockholders", "total equity")

            ratios = {
                "filing_date": str(f.filing_date),
                "report_date": str(getattr(f, "report_date", None)),
            }
            if revenue and revenue != 0:
                if gross_profit is not None:
                    ratios["gross_margin"] = round(gross_profit / revenue * 100, 2)
                if operating_income is not None:
                    ratios["operating_margin"] = round(operating_income / revenue * 100, 2)
                if net_income is not None:
                    ratios["net_margin"] = round(net_income / revenue * 100, 2)
            if net_income is not None:
                if total_assets and total_assets != 0:
                    ratios["roa"] = round(net_income / total_assets * 100, 2)
                if total_equity and total_equity != 0:
                    ratios["roe"] = round(net_income / total_equity * 100, 2)

            ratios["raw"] = {"revenue": revenue, "gross_profit": gross_profit,
                             "operating_income": operating_income, "net_income": net_income,
                             "total_assets": total_assets, "total_equity": total_equity}
            ratios_list.append(ratios)
        except Exception as e:
            ratios_list.append({"filing_date": str(f.filing_date), "error": str(e)})
            continue

    return {"ok": True, "ticker": args.ticker, "form": args.form, "ratios": ratios_list}


def cmd_list_filings(args):
    c = _retry(lambda: Company(args.ticker))
    filings = _retry(lambda: c.get_filings(form=args.form)) if args.form else _retry(lambda: c.get_filings())
    if len(filings) == 0:
        return {"ok": False, "error": f"No filings found for {args.ticker}."}

    df = filings.head(args.limit).to_pandas()
    if "report_date" not in df.columns and "reportDate" in df.columns:
        df["report_date"] = df["reportDate"]
    if "accession_no" not in df.columns and "accession_number" in df.columns:
        df["accession_no"] = df["accession_number"]

    desired = ["form", "filing_date", "report_date", "accession_no"]
    available = [c for c in desired if c in df.columns]
    rows = df[available].to_dict(orient="records") if available else df.to_dict(orient="records")
    return {"ok": True, "ticker": args.ticker, "count": len(rows), "rows": rows}


def cmd_eight_k(args):
    c = _retry(lambda: Company(args.ticker))
    filings = _retry(lambda: c.get_filings(form="8-K"))
    if len(filings) == 0:
        return {"ok": False, "error": f"No 8-K filings found for {args.ticker}."}

    rows = []
    for f in filings.head(args.limit):
        row = {"filing_date": str(f.filing_date), "accession_no": str(getattr(f, "accession_no", ""))}
        try:
            obj = f.obj()
            row["items"] = str(getattr(obj, "items", "N/A"))
            text = ""
            if hasattr(obj, "text") and callable(obj.text):
                text = obj.text()
            elif hasattr(f, "text") and callable(f.text):
                text = f.text()
            else:
                text = str(obj)
            row["content_snippet"] = text[:3000]
        except Exception as e:
            row["error"] = str(e)
        rows.append(row)
    return {"ok": True, "ticker": args.ticker, "count": len(rows), "rows": rows}


def cmd_insider(args):
    c = _retry(lambda: Company(args.ticker))
    filings = _retry(lambda: c.get_filings(form="4"))
    if len(filings) == 0:
        return {"ok": False, "error": f"No Form 4 filings found for {args.ticker}."}

    rows = []
    for f in filings.head(args.limit):
        row = {"filing_date": str(f.filing_date), "accession_no": str(getattr(f, "accession_no", ""))}
        try:
            row["content"] = str(f.obj())[:3000]
        except Exception as e:
            row["error"] = str(e)
        rows.append(row)
    return {"ok": True, "ticker": args.ticker, "count": len(rows), "rows": rows}


def cmd_search_text(args):
    c = _retry(lambda: Company(args.ticker))
    filings = _retry(lambda: c.get_filings(form=args.form))
    if len(filings) == 0:
        return {"ok": False, "error": f"No {args.form} filings found for {args.ticker}."}

    results = []
    for f in filings.head(args.limit):
        filing_result = {"filing_date": str(f.filing_date), "form": str(f.form), "matches": []}
        try:
            text = ""
            if hasattr(f, "text") and callable(f.text):
                text = f.text()
            else:
                obj = f.obj()
                if hasattr(obj, "text") and callable(obj.text):
                    text = obj.text()
                else:
                    text = str(obj)

            keyword = args.keyword.lower()
            text_lower = text.lower()
            start = 0
            match_count = 0
            while True:
                idx = text_lower.find(keyword, start)
                if idx == -1:
                    break
                match_count += 1
                if match_count > args.max_matches:
                    break
                win_start = max(0, idx - args.window)
                win_end = min(len(text), idx + len(keyword) + args.window)
                snippet = " ".join(text[win_start:win_end].split())
                filing_result["matches"].append({"index": match_count, "snippet": snippet})
                start = idx + len(keyword)
            filing_result["total_matches"] = match_count
        except Exception as e:
            filing_result["error"] = str(e)
        results.append(filing_result)
    return {"ok": True, "ticker": args.ticker, "keyword": args.keyword, "filings": results}


def cmd_read_item(args):
    c = _retry(lambda: Company(args.ticker))
    filings = _retry(lambda: c.get_filings(form=args.form)).head(1)
    if len(filings) == 0:
        return {"ok": False, "error": f"No {args.form} filings found for {args.ticker}."}

    f = filings[0]
    result = {"filing_date": str(f.filing_date), "form": str(f.form), "item": args.item}
    try:
        obj = f.obj()
        if hasattr(obj, "__getitem__"):
            try:
                text = obj[args.item]
                if not text:
                    return {"ok": False, "error": f"{args.item} is empty.", **result}
                truncated = len(text) > args.max_chars
                result["text"] = text[:args.max_chars]
                result["truncated"] = truncated
                result["total_chars"] = len(text)
                return {"ok": True, "ticker": args.ticker, **result}
            except KeyError:
                available = str(getattr(obj, "items", "Unknown"))
                return {"ok": False, "error": f"Item '{args.item}' not found. Available: {available}", **result}
        else:
            return {"ok": False, "error": "Filing object does not support item extraction.", **result}
    except Exception as e:
        return {"ok": False, "error": str(e), **result}


def cmd_ask(args):
    """NL router: parse question → route to appropriate command."""
    question = args.question
    cmd, ctx = _route_nl(question)
    tickers = ctx.get("tickers", [])

    if not tickers:
        return {"ok": False, "error": "Could not identify any ticker in your question.",
                "hint": "Include a stock ticker like AAPL, NVDA, MSFT in your question."}

    ticker = tickers[0]

    if cmd == "eight-k":
        ns = argparse.Namespace(ticker=ticker, limit=3, json=True, csv=False)
        return cmd_eight_k(ns)

    elif cmd == "insider":
        ns = argparse.Namespace(ticker=ticker, limit=5, json=True, csv=False)
        return cmd_insider(ns)

    elif cmd == "ratios":
        ns = argparse.Namespace(ticker=ticker, form="10-K", limit=1, json=True, csv=False)
        return cmd_ratios(ns)

    elif cmd == "compare" and len(tickers) >= 2:
        # Extract keyword from question
        kw = _extract_metric_keyword(question)
        ns = argparse.Namespace(tickers=f"{tickers[0]},{tickers[1]}", keyword=kw, form="10-K", limit=5, json=True, csv=False)
        return cmd_compare(ns)

    elif cmd == "financials":
        ns = argparse.Namespace(ticker=ticker, form="10-K", limit=1, statement="all", json=True, csv=False)
        return cmd_financials(ns)

    elif cmd == "read-item":
        item = "Item 1"
        if re.search(r"item\s*7|md.?a|discussion", question, re.IGNORECASE):
            item = "Item 7"
        elif re.search(r"item\s*8|financial statement|notes", question, re.IGNORECASE):
            item = "Item 8"
        ns = argparse.Namespace(ticker=ticker, form="10-K", item=item, max_chars=15000, json=True, csv=False)
        return cmd_read_item(ns)

    elif cmd == "search-text":
        kw = _extract_search_keyword(question)
        ns = argparse.Namespace(ticker=ticker, keyword=kw, form="10-K", limit=1, window=500, max_matches=10, json=True, csv=False)
        return cmd_search_text(ns)

    elif cmd == "smart-facts":
        kw = _extract_metric_keyword(question)
        ns = argparse.Namespace(ticker=ticker, keyword=kw, form="10-K", limit=5, question=question, json=True, csv=False)
        return cmd_smart_facts(ns)

    else:
        return {"ok": False, "error": f"Could not determine what data to fetch for: {question}",
                "hint": "Try including specific terms like 'revenue', '8-K', 'insider', 'gross margin', 'business model'."}


def _extract_metric_keyword(question: str) -> str:
    """Extract the financial metric keyword from a question."""
    q_lower = question.lower()
    metric_map = [
        ("gross margin", "GrossProfit"), ("gross profit", "GrossProfit"),
        ("net income", "NetIncome"), ("net margin", "NetIncome"),
        ("operating income", "OperatingIncome"), ("operating margin", "OperatingIncome"),
        ("revenue", "Revenue"), ("sales", "Revenue"),
        ("ebitda", "EBITDA"), ("capex", "CapitalExpenditure"),
        ("eps", "EarningsPerShare"), ("earnings per share", "EarningsPerShare"),
        ("total assets", "Assets"), ("total debt", "Debt"),
        ("free cash flow", "FreeCashFlow"), ("cash flow", "CashFlow"),
        ("roe", "ReturnOnEquity"), ("roa", "ReturnOnAssets"),
        ("r&d", "ResearchAndDevelopment"), ("research", "ResearchAndDevelopment"),
        ("sga", "SellingGeneralAndAdministrative"),
        ("depreciation", "Depreciation"), ("amortization", "Amortization"),
        ("毛利", "GrossProfit"), ("净利润", "NetIncome"), ("收入", "Revenue"), ("营收", "Revenue"),
    ]
    for trigger, concept_kw in metric_map:
        if trigger in q_lower:
            return concept_kw
    # Fallback: use first capitalized word that looks like a metric
    return "Revenue"


def _extract_search_keyword(question: str) -> str:
    """Extract keyword for text search."""
    q_lower = question.lower()
    kw_map = [
        ("depreciation", "useful life"), ("折旧", "useful life"),
        ("lease", "lease term"), ("rent", "lease term"),
        ("accounting polic", "accounting policy"), ("会计政策", "accounting policy"),
        ("server", "server"), ("data center", "data center"),
    ]
    for trigger, kw in kw_map:
        if trigger in q_lower:
            return kw
    return "policy"


def cmd_self_test(args):
    checks = []

    def run_check(name, fn):
        try:
            fn()
            checks.append({"name": name, "ok": True})
        except Exception as e:
            checks.append({"name": name, "ok": False, "error": str(e), "hint": _error_hint(e)})

    run_check("identity", lambda: (_ for _ in ()).throw(RuntimeError("EDGAR_IDENTITY missing")) if "EDGAR_IDENTITY" not in os.environ else None)

    def _check_filings():
        c = Company(args.ticker)
        f = c.get_filings(form="10-K")
        if len(f) == 0:
            raise RuntimeError(f"No 10-K filings for {args.ticker}")

    def _check_facts():
        c = Company(args.ticker)
        facts = c.get_facts()
        if not facts:
            raise RuntimeError(f"No facts for {args.ticker}")

    run_check("list-filings", _check_filings)
    run_check("facts", _check_facts)

    return {"ok": all(c["ok"] for c in checks), "ticker": args.ticker, "checks": checks}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="findata-analyst v2 — SEC EDGAR data with NL routing")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--csv", action="store_true", help="CSV output (for rows-based commands)")
    sub = parser.add_subparsers(dest="command", required=True)

    # ask (NL router)
    p = sub.add_parser("ask")
    p.add_argument("question", nargs="+")
    p.set_defaults(func=lambda a: cmd_ask(argparse.Namespace(question=" ".join(a.question), json=a.json, csv=a.csv)))

    # financials
    p = sub.add_parser("financials")
    p.add_argument("--ticker", required=True)
    p.add_argument("--form", default="10-K")
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--statement", default="all")
    p.set_defaults(func=cmd_financials)

    # search-concepts
    p = sub.add_parser("search-concepts")
    p.add_argument("--ticker", required=True)
    p.add_argument("--keyword", required=True)
    p.set_defaults(func=cmd_search_concepts)

    # facts
    p = sub.add_parser("facts")
    p.add_argument("--ticker", required=True)
    p.add_argument("--concept", required=True)
    p.add_argument("--form", default="10-K")
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=cmd_facts)

    # smart-facts (auto concept search + fetch)
    p = sub.add_parser("smart-facts")
    p.add_argument("--ticker", required=True)
    p.add_argument("--keyword", required=True)
    p.add_argument("--form", default="10-K")
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=lambda a: cmd_smart_facts(argparse.Namespace(ticker=a.ticker, keyword=a.keyword, form=a.form, limit=a.limit, question="")))

    # compare
    p = sub.add_parser("compare")
    p.add_argument("--tickers", required=True, help="Two tickers comma-separated (e.g., NVDA,AVGO)")
    p.add_argument("--keyword", required=True)
    p.add_argument("--form", default="10-K")
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=cmd_compare)

    # ratios
    p = sub.add_parser("ratios")
    p.add_argument("--ticker", required=True)
    p.add_argument("--form", default="10-K")
    p.add_argument("--limit", type=int, default=1)
    p.set_defaults(func=cmd_ratios)

    # list-filings
    p = sub.add_parser("list-filings")
    p.add_argument("--ticker", required=True)
    p.add_argument("--form", default="")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_list_filings)

    # eight-k
    p = sub.add_parser("eight-k")
    p.add_argument("--ticker", required=True)
    p.add_argument("--limit", type=int, default=3)
    p.set_defaults(func=cmd_eight_k)

    # insider
    p = sub.add_parser("insider")
    p.add_argument("--ticker", required=True)
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=cmd_insider)

    # search-text
    p = sub.add_parser("search-text")
    p.add_argument("--ticker", required=True)
    p.add_argument("--keyword", required=True)
    p.add_argument("--form", default="10-K")
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--window", type=int, default=500)
    p.add_argument("--max_matches", type=int, default=10)
    p.set_defaults(func=cmd_search_text)

    # read-item
    p = sub.add_parser("read-item")
    p.add_argument("--ticker", required=True)
    p.add_argument("--form", default="10-K")
    p.add_argument("--item", required=True)
    p.add_argument("--max_chars", type=int, default=15000)
    p.set_defaults(func=cmd_read_item)

    # self-test
    p = sub.add_parser("self-test")
    p.add_argument("--ticker", default="AAPL")
    p.set_defaults(func=cmd_self_test)

    args = parser.parse_args()
    try:
        result = args.func(args)
        _emit(result, as_json=args.json, as_csv=args.csv)
    except Exception as e:
        _emit(_error_json(e), as_json=args.json)
        sys.exit(1)


if __name__ == "__main__":
    main()
