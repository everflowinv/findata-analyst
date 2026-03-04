---
name: findata-analyst
description: SEC Financial Data Analyst skill. Retrieves financial statements, searches XBRL concepts, fetches historical data, parses 8-K/Form 4, and performs qualitative text search within specific filings (e.g., Business Models, Depreciation policies) using edgartools.
---
# Financial Data Analyst Skill

## Setup & Installation

⚠️ **CRITICAL: Installation Location**
Do NOT install this skill in the global `node_modules` directory. You MUST clone/install it directly into your local OpenClaw workspace (`~/.openclaw/workspace/skills/`).

**Configuration:**
Set your SEC Edgar identity (Required by SEC API to avoid rate limits/bans):
`export EDGAR_IDENTITY="Your Name <your.email@example.com>"`

**That's it!** The skill is fully auto-bootstrapping. The first time the Agent executes the `run.sh` command, it will automatically create an isolated virtual environment and install everything needed (`edgartools` and `pandas`).

---

## When to use this skill
Use this skill when a user asks complex financial or qualitative questions requiring data from SEC filings. Examples:
- **Corporate Events/News:** "Affirm 最近几个 8-K 讲了什么？"
- **Insider Trading:** "Affirm 最近三个月的高管增减持明细"
- **Margins & Ratios:** "Nvidia 和 Broadcom 哪个 GPM 高?"
- **Historical Metrics:** "Microsoft 每个 segment 过去三年的收入增速是多少?"
- **Qualitative Research:** "AJG 的商业模式是怎么样的？", "Amazon 和 Meta 对服务器的折旧年限有什么区别？"

## How to use this skill
⚠️ **AGENT INSTRUCTION: DO NOT write or execute your own Python code to fetch data.** 
You MUST use your terminal/bash execution tool to run the following exact commands to interact with the Python backend.

⚠️ **CRITICAL: NO HALLUCINATIONS.** 
You are STRICTLY FORBIDDEN from inventing numbers, policies, or financial metrics. Every single number and qualitative statement you provide MUST be retrieved directly from the `edgartools` outputs. If the tool does not return the data, say you cannot find it. ALWAYS cite your source (e.g., "According to the latest 10-K...").

⚠️ **CRITICAL: FACTS YEAR INTERPRETATION RULE (HIGHEST PRIORITY).**
When using `facts` output:
- Treat `period_end + numeric_value + unit` as the source of truth.
- Treat `fiscal_year_raw` as metadata only (can be wrong/misaligned in some filings).
- If `year_mismatch=True`, you MUST NOT narrate conclusions by `fiscal_year_raw` only.
- In final answers, anchor each metric to `period_end` date explicitly.

You must use iterative logic to answer the user's question:
1. **Identify Tickers:** Extract company names from the prompt and map them to their stock tickers (e.g., Affirm -> AFRM, AJG -> AJG).
2. **Determine the Command:** Decide what data you need to answer the question.
3. **Execute & Iterate:** Run the command. If you need more info or the text is truncated, adjust your query (e.g., switch from `read-item` to `search-text` with specific keywords).
4. **Calculate & Summarize:** Present the final answer clearly to the user, citing the exact SEC filing used.

---

### Commands Syntax

#### 0. Health Check (Recommended Before First Use)
Use this to verify environment, EDGAR identity, and SEC connectivity.
```bash
bash skills/findata-analyst/run.sh self-test --ticker [TICKER]
```

#### 1. Fetch Latest 8-K Filings (News & Events)
Use to answer questions about recent corporate events, earnings releases, or changes in management.
```bash
bash skills/findata-analyst/run.sh eight-k --ticker [TICKER] --limit [NUM]
```

#### 2. Fetch Insider Trading (Form 4)
Use to answer questions about executive buys/sells. Automatically prints parsed ownership tables.
```bash
bash skills/findata-analyst/run.sh insider --ticker [TICKER] --limit [NUM]
```

#### 3. Read Specific Qualitative Sections (e.g., Business Models)
Use this to read a specific "Item" from a 10-K or 10-Q. 
- "Item 1" = Business Model / Operations overview.
- "Item 7" = Management's Discussion and Analysis (MD&A).
- "Item 8" = Financial Statements and Supplementary Data (where accounting policies and notes live).
```bash
bash skills/findata-analyst/run.sh read-item --ticker [TICKER] --form [10-K|10-Q] --item "[ITEM_NAME]"
```
*Example:* `bash skills/findata-analyst/run.sh read-item --ticker AJG --form 10-K --item "Item 1"`

#### 4. Keyword Search within Filings (e.g., Depreciation Policies)
Use this when you need highly specific information (like server depreciation, lease terms, specific risks) that is buried deep in a 10-K (usually in the Notes to Financial Statements). It returns a +/- 500 character snippet around every match.
```bash
bash skills/findata-analyst/run.sh search-text --ticker [TICKER] --keyword "[KEYWORD]" --form 10-K
```
*Example:* `bash skills/findata-analyst/run.sh search-text --ticker AMZN --keyword "useful life"` (or "depreciation")

#### 5. Fetch Standard Financial Statements
Use this when you need standard figures like Total Revenue, Gross Profit, Net Income.
```bash
bash skills/findata-analyst/run.sh financials --ticker [TICKER] --form [10-K|10-Q] --statement [income|balance|cash|all] --limit [NUM]
```

#### 6. Search Specific XBRL Concepts & Fetch Historical Data
Use this when looking for non-standard line items like segment data or specific operational metrics. First search the name, then fetch the history.
```bash
bash skills/findata-analyst/run.sh search-concepts --ticker [TICKER] --keyword [KEYWORD]
bash skills/findata-analyst/run.sh facts --ticker [TICKER] --concept [EXACT_CONCEPT_STRING] --form [10-K|10-Q] --limit [NUM_YEARS]
```

---

### JSON Output Mode (for deterministic parsing)

For machine-consumable output, add global `--json` before the subcommand:
```bash
bash skills/findata-analyst/run.sh --json facts --ticker MSFT --concept us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax --form 10-K --limit 3
bash skills/findata-analyst/run.sh --json financials --ticker AAPL --form 10-K --statement income --limit 1
bash skills/findata-analyst/run.sh --json self-test --ticker AAPL
```

### Output Formatting Rules (Mandatory)

For any answer that includes values from `facts`:
1. Use this sentence pattern:
   - "As of `YYYY-MM-DD` (`period_end`), `[concept/metric]` was `[numeric_value] [unit]`."
2. If `year_mismatch=True` appears in any row used in your answer, append:
   - "Note: filing metadata year (`fiscal_year_raw`) is inconsistent with `period_end`; interpretation is based on `period_end`."
3. Never aggregate or label years solely by `fiscal_year_raw`.

### Forbidden Behaviors (Mandatory)

- Do NOT present multiple different `period_end` rows as if they belong to the same fiscal year only because `fiscal_year_raw` matches.
- Do NOT compute YoY/CAGR unless the compared rows are explicitly identified by `period_end`.
- Do NOT claim accounting policy details unless a `read-item` or `search-text` snippet is shown.
- Do NOT omit source context; every quantitative claim must include ticker + form + period reference.

### Minimum Self-Check Before Final Answer

Before replying, verify all 4 items:
1. Correct ticker(s) and form(s) were queried.
2. Numbers are tied to `period_end` (not only fiscal year labels).
3. Any `year_mismatch=True` rows are disclosed with a caution note.
4. Source citation includes command context (e.g., latest 10-K / Form 4 / 8-K snippet).

If any item fails, do NOT guess. State what is missing and run another command.

### Qualitative Workflow Examples

**User Ask:** "AJG 的商业模式是怎么样的？"
*Agent Action:* 
1. Determine that business models are described in "Item 1" of a 10-K.
2. Run `bash skills/findata-analyst/run.sh read-item --ticker AJG --form 10-K --item "Item 1"`.
3. Read the output text (which provides the official company description).
4. Summarize the core business segments, revenue streams, and market position based *strictly* on that text.

**User Ask:** "Amazon 和 Meta 对服务器的折旧年限有什么区别？"
*Agent Action:*
1. Depreciation policies are usually buried in the "Notes to Consolidated Financial Statements" inside a 10-K.
2. Run `bash skills/findata-analyst/run.sh search-text --ticker AMZN --keyword "useful life" --form 10-K`. (If that fails, try keyword "server" or "depreciation"). Read the returned snippet (e.g., "Servers are depreciated over 4 to 5 years...").
3. Run `bash skills/findata-analyst/run.sh search-text --ticker META --keyword "useful life" --form 10-K`.
4. Compare the findings and present the exact numbers, citing the latest 10-Ks.