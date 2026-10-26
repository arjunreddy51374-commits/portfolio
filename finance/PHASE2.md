# Finance Phase 2: calculation, history, and scenario prototype

## Scope and operating boundary

This release adds one source-attributed calculation contract shared by manual entry, multi-period history, CSV/XLSX import, and scenario modelling. It is a stateless Flask prototype. It does not retrieve source documents, persist uploads or assumptions, publish analysis, authenticate Finance users, or provide AI-generated conclusions. Source URLs are retained as references and are not fetched or independently verified.

All public-source inputs and forecasts remain unreviewed. A human must reconcile figures and definitions to the cited source before using a result externally. The fictional Meridian case is synthetic and must never be represented as a reported company.

## Calculation contract

Amounts are parsed as decimal strings, normalized to base currency units, and calculated with Python `Decimal`. The interface rounds only the display to two decimal places; the returned decimal value retains calculation precision. Every computed metric includes a formula identifier/version and the input field references used.

Supported metrics and definitions:

| Metric | Formula | Required interpretation |
| --- | --- | --- |
| Revenue growth | `(revenue - prior revenue) / prior revenue × 100` | Prior period must be earlier and of the same annual/quarterly frequency; prior revenue must be positive. |
| Gross, operating, net margin | `profit / revenue × 100` | Revenue must be positive; source classifications can differ. |
| EBITDA | `operating profit + depreciation and amortisation` | Requires explicit depreciation/amortisation input; it is not inferred. |
| Working capital | `current assets - current liabilities` | Point-in-time amount. |
| Current, quick ratio | `current assets / current liabilities`; `(current assets - inventory) / current liabilities` | Current liabilities must be positive. |
| Debt / equity | `total debt / total equity` | Equity must be positive for a meaningful ratio. |
| Net debt | `total debt - cash and equivalents` | Cash and total debt definitions must be aligned. |
| Interest coverage | `operating profit / interest expense` | Interest expense must be positive. |
| ROA / ROE | `net income / average assets or equity × 100` | Uses supplied period averages, not period-end balances. |
| Asset turnover | `revenue / average total assets` | Average assets must be positive. |
| Inventory turnover / days | `cost of revenue / average inventory`; `average inventory / cost of revenue × 365` | Cost of revenue and average inventory must be positive. |
| Receivables / payables days | `average receivables / revenue × 365`; `average payables / cost of revenue × 365` | Revenue and cost of revenue must be positive. |
| Working-capital days | `(current assets - current liabilities) / revenue × 365` | This is a broad working-capital measure, not a cash forecast. |
| Cash conversion cycle | `inventory days + receivables days - payables days` | Requires all three average balance inputs and positive revenue/cost of revenue. |
| Operating cash flow / net income | `operating cash flow / net income` | Not meaningful at zero net income; investigate one-off and working-capital effects. |
| Free cash flow | `operating cash flow - capital expenditure` | Capex is entered as a positive outflow. |
| Cash-flow category subtotal | `operating + investing + financing cash flow` | Not necessarily reported net cash change without FX and other reconciliation items. |

Absent values remain `MISSING`; explicitly marked not-applicable fields remain `NOT_APPLICABLE`; invalid denominator cases are `INVALID`. These states do not substitute for source review.

## Multi-period history and import

`POST /api/finance/history` accepts 2–20 annual or quarterly statements for one company. It rejects duplicate periods, mixed currencies, mixed annual/quarterly frequencies, and mixed illustrative/reported data. Units may vary because the calculation engine normalizes amounts first. Revenue growth is calculated only across adjacent supplied periods; missing periods are called out. Annual revenue CAGR uses the actual first-to-last year span.

`POST /api/finance/import` accepts `.csv` and `.xlsx`, up to 5 MiB. Each row needs company, period, currency, unit, and an HTTPS source URL. Imports are limited to 500 rows, 20 companies, and 20 periods per company. XLSX formula cells are rejected and only the first worksheet is read. Data is validated in memory and discarded after the response. Peer comparisons are withheld across currencies when no reviewed FX conversion is available.

The blank [CSV template](../static/documents/finance-import-template.csv) shows the import header. The importer does not infer figures from PDFs or retrieve source links.

## Annual scenario model

`POST /api/finance/model` requires a complete annual opening statement with current and non-current assets, current liabilities, debt, other liabilities, and equity. The opening balance sheet must balance within a stated tolerance. Forecasts are produced for 1–10 years in the source currency and unit. Each projected value returns the model formula version and input references.

The model calculates revenue, gross profit, operating expenses/profit, EBITDA, interest, tax, net income, operating cash flow, capex, financing, cash, working capital, debt, non-current assets, equity, total assets, and balance difference. The balance sheet rolls forward using the same assumptions used by the income and cash-flow statements. Negative ending cash/assets and inventory inconsistencies are surfaced as warnings or rejected.

Base assumptions are derived where the corresponding source inputs exist. Otherwise the visible defaults are 10% revenue growth, 25% tax rate, 8% debt interest, 3.5% depreciation/revenue, 6.5% capex/revenue, zero dividend payout, and zero annual debt change. Gross margin, operating expense/revenue, non-cash current assets/revenue, current liabilities/revenue, inventory/revenue, and prior-period revenue growth are derived from inputs when available. Users can edit every assumption before running the scenarios.

Downside and upside scenarios shift annual revenue growth by the user-selected percentage points and operating margin by the user-selected percentage points. They are deterministic sensitivities, not probability-weighted cases or forecasts. Other liabilities are held constant; tax benefits are not modelled when pre-tax income is negative.

## Demonstration case (synthetic)

Meridian Consumer Products is a fictional INR-million example:

| Period | Revenue | Gross profit | Operating profit | Net income |
| --- | ---: | ---: | ---: | ---: |
| FY2023 | 720 | 302.4 | 108 | 66.24 |
| FY2024 | 870 | 365.4 | 130.5 | 80.04 |
| FY2025 | 1,000 | 420 | 150 | 92 |

The FY2025 opening balance sheet has current assets 600 (including 100 cash and 120 inventory), non-current assets 500, current liabilities 300, debt 240, other liabilities 160, and equity 400. Assets total 1,100 and liabilities plus equity total 1,100. Independent arithmetic checks produce 14.94% revenue growth from FY2024, 42% gross margin, 15% operating margin, 300 working capital, 2.00 current ratio, 1.60 quick ratio, 0.60 debt/equity, 140 net debt, and 45 free cash flow from operating cash flow 110 less capex 65. The 2023–2025 revenue CAGR is 17.85% using two elapsed years.

These values exist only to exercise formulas and page interactions. They are not sourced from a real issuer and are not suitable for investment decisions.

## Release checks and known limits

Automated tests cover independently computed examples, decimal normalization, missing/invalid states, period and currency checks, gap handling, scenario ordering and balance reconciliation, negative-cash warnings, CSV/XLSX validation, and the Flask routes. The current UI uses native responsive HTML controls and horizontally scrollable result tables.

The Finance prototype still needs an approved source policy and connector design before automated retrieval, a user/role model before protected persistence or publication, and deployment-specific evidence for backups/recovery, monitoring, and access control. It makes no production-readiness claim and does not implement valuation, external entity mapping, source document extraction, or AI commentary.
