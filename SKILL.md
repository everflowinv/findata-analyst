---
name: findata-analyst
description: SEC Financial Data Analyst skill. Retrieves financial statements, searches XBRL concepts, fetches historical data, parses 8-K/Form 4, calculates ratios, compares companies, and performs qualitative text search within SEC filings using edgartools. Supports natural language queries via --ask.
---

# Financial Data Analyst Skill v2

⚠️ **CRITICAL: NO HALLUCINATIONS** — Every number must come from command output. If the tool doesn't return data, say so.
⚠️ **CRITICAL: EXECUTION RULE** — Always run through `bash skills/findata-analyst/run.sh ...`. Do NOT write ad-hoc Python.

## Setup
```bash
export EDGAR_IDENTITY="Your Name <your.email@example.com>"  # Required by SEC
```
First run auto-creates venv and installs dependencies.

---

## 0) Preflight
```bash
bash skills/findata-analyst/run.sh --json self-test --ticker AAPL
```

---

## 1) Natural Language Entry (Recommended)

For any SEC/EDGAR question, prefer the `ask` command — it auto-routes to the correct subcommand:

```bash
bash skills/findata-analyst/run.sh --json ask NVDA gross margin trend
bash skills/findata-analyst/run.sh --json ask AAPL recent 8-K filings
bash skills/findata-analyst/run.sh --json ask TSLA insider trading
bash skills/findata-analyst/run.sh --json ask "NVDA vs AVGO which has higher gross margin"
bash skills/findata-analyst/run.sh --json ask AMZN depreciation policy
bash skills/findata-analyst/run.sh --json ask AJG business model
```

### What `ask` can detect and route:
| Question Pattern | Routes To |
|---|---|
| "8-K", "corporate event", "earnings release" | `eight-k` |
| "insider", "form 4", "executive buy/sell" | `insider` |
| "gross margin", "ROE", "ROA", "profit margin" | `ratios` |
| "compare", "vs", "versus", two tickers | `compare` |
| "income statement", "balance sheet", "cash flow" | `financials` |
| "business model", "what does X do", "Item 1" | `read-item` |
| "depreciation", "useful life", "accounting policy" | `search-text` |
| "segment", "revenue by", "breakdown", "xbrl" | `smart-facts` |
| "revenue", "earnings", "net income", "trend" | `smart-facts` |

If the question doesn't match any pattern but contains a ticker, it falls back to `smart-facts`.

---

## 2) Full Command Catalog (14 commands)

### NL & Utility
| Command | Use Case | Key Args |
|---------|----------|----------|
| `ask` | Natural language router (recommended entry point) | positional: question words |
| `self-test` | Verify EDGAR connectivity and setup | `--ticker AAPL` (optional) |

### Financial Data
| Command | Use Case | Key Args |
|---------|----------|----------|
| `financials` | Full financial statements (income/balance/cash) | `--ticker --form 10-K --statement all\|income\|balance\|cash --limit 1` |
| `ratios` | Auto-calculated financial ratios from latest filing | `--ticker --form 10-K --limit 1` |
| `smart-facts` | One-step: auto find best XBRL concept + fetch time series | `--ticker --keyword Revenue --form 10-K --limit 5` |
| `facts` | Fetch exact XBRL concept time series (need concept ID) | `--ticker --concept us-gaap:Revenue --form 10-K --limit 5` |
| `search-concepts` | Find available XBRL concept IDs for a ticker | `--ticker --keyword Revenue` |
| `compare` | Compare a metric across two tickers side-by-side | `--tickers NVDA,AVGO --keyword GrossProfit --form 10-K` |

### Filings & Events
| Command | Use Case | Key Args |
|---------|----------|----------|
| `eight-k` | Recent 8-K events (earnings, material events) | `--ticker --limit 3` |
| `insider` | Form 4 insider trading filings | `--ticker --limit 5` |
| `list-filings` | Browse all filings by form type | `--ticker --form 10-K --limit 10` |

### Qualitative Research
| Command | Use Case | Key Args |
|---------|----------|----------|
| `read-item` | Read specific 10-K/10-Q section (max 15K chars) | `--ticker --form 10-K --item "Item 1"` |
| `search-text` | Keyword search within filing full text | `--ticker --keyword "useful life" --form 10-K --window 500 --max_matches 10` |

### Common Items for `read-item`
- **Item 1** — Business description / operations overview
- **Item 1A** — Risk factors
- **Item 7** — MD&A (Management's Discussion and Analysis)
- **Item 8** — Financial statements & notes

---

## 3) Output Modes

All commands support `--json` (structured JSON) and `--csv` (for rows-based output):
```bash
bash skills/findata-analyst/run.sh --json ratios --ticker AAPL
bash skills/findata-analyst/run.sh --csv list-filings --ticker AAPL --limit 5
```

### JSON Output Structure
Every response follows the same envelope:
```json
{"ok": true, "ticker": "AAPL", ...command-specific fields...}
{"ok": false, "error": "description", "hint": "actionable suggestion"}
```

### `ratios` returns these metrics (when available):
- `gross_margin` — Gross Profit / Revenue × 100
- `operating_margin` — Operating Income / Revenue × 100
- `net_margin` — Net Income / Revenue × 100
- `roa` — Net Income / Total Assets × 100
- `roe` — Net Income / Total Equity × 100
- `raw` — underlying absolute values (revenue, gross_profit, operating_income, net_income, total_assets, total_equity)

### `smart-facts` returns:
- `chosen_concept` — the best-matching XBRL concept (prefers non-deprecated, us-gaap)
- `all_concepts` — top 5 candidate concepts found
- `rows` — time series with `period_end`, `numeric_value`, `unit`, `year_mismatch`

---

## 4) Facts Interpretation Rule (CRITICAL)

When using `facts` or `smart-facts` output:
- Anchor metrics to **`period_end`** (source of truth), NOT `fiscal_year_raw`
- If `year_mismatch=True`, **disclose it** in your answer
- Pattern: "As of `period_end`, `metric` was `value` `unit`."

---

## 5) Output Formatting for Users

1. **结论/Answer (1-3 lines)** — Direct answer first
2. **Key Data (bullets)** — Only from command output, with units and periods
3. **Source (bullet)** — ticker + form type + filing date
4. **Caveats (if needed)** — year_mismatch, missing data, truncation

⚠️ Never output markdown tables on Discord/WhatsApp; use bullet lists instead.

---

## 6) Features

- **Retry mechanism** — Transient SEC errors (timeout, TLS, rate-limit) auto-retry up to 2× with exponential backoff
- **Disk cache** — Concepts and facts cached 24h in `scripts/.cache/` to reduce SEC API calls
- **Error hints** — Failed calls return actionable `hint` field (e.g., "Set EDGAR_IDENTITY", "Use search-concepts first")
- **Market coverage** — Any US-listed company with SEC EDGAR filings (stocks, ETFs, REITs, BDCs, SPACs)

---

## 7) Workflow Examples

**"NVDA 和 AVGO 哪个毛利率高?"**
```bash
bash skills/findata-analyst/run.sh --json compare --tickers NVDA,AVGO --keyword GrossProfit
```

**"AJG 的商业模式是怎么样的?"**
```bash
bash skills/findata-analyst/run.sh --json read-item --ticker AJG --form 10-K --item "Item 1"
```

**"Amazon 的服务器折旧年限?"**
```bash
bash skills/findata-analyst/run.sh --json search-text --ticker AMZN --keyword "useful life" --form 10-K
```

**"Microsoft 过去三年收入趋势"**
```bash
bash skills/findata-analyst/run.sh --json smart-facts --ticker MSFT --keyword Revenue --limit 3
```

**"Apple 最新一期财务比率"**
```bash
bash skills/findata-analyst/run.sh --json ratios --ticker AAPL
```

**"Tesla 最近有什么 insider 交易?"**
```bash
bash skills/findata-analyst/run.sh --json insider --ticker TSLA --limit 5
```
