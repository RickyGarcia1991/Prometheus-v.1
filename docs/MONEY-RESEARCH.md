# Money and investing research

Open **Maps & Studio → Money & investing**. The application has 26 official resources: Pennsylvania and New Jersey unclaimed property, US benefits and tax-credit guidance, unpaid wages, pensions, failed-bank/credit-union funds, bankruptcy funds, FHA refunds, jobs, business counseling and contracting. Selected Canadian, UK and Australian sources are included. This is a starting directory, not worldwide completeness or a finding that money is owed to you. Source review date: October 9, 2026.

Search old states and addresses directly through the official agencies. Enter identity documents only on the agency's own service. No automated personal searches, claims, brokerage access or paid service purchases occur in Prometheus. International benefits require an actual entitlement; location alone does not make a person eligible.

The stock checklist covers the business, cash flow, debt, valuation, personal risk and costs. Its score is research completeness, not predicted return. [SEC filings](https://www.sec.gov/edgar/search/) and [Investor.gov](https://www.investor.gov/introduction-investing/investing-basics/investment-products/stocks) provide original disclosures and education. Current financial-data API documentation is linked, but no live stock-price feed is connected.

## Algorithm lab

Import a CSV with `date,asset,benchmark`, containing 70–5,000 aligned daily observations in increasing date order. Dates use YYYY-MM-DD. Both series must have positive adjusted closing values or total-return indices in consistent currencies. Record the publisher, instruments, download date and adjustments. The format example contains synthetic rows only and is too short for a test. Nothing is uploaded.

The baseline rule holds the asset when its previous closing value exceeds its moving average, otherwise holds cash. At close i, the signal uses only observations through i−1, and earns the close-i to close-(i+1) return. This extra observation lag avoids trading on an unknown current close. The moving-average window is 20 observations by default. The first 70% of observations precede the test; parameters are not fitted or optimized. At least 20 test intervals must remain after warm-up.

The report compares directional accuracy with an always-up baseline, and compares after-cost return and maximum closing-value drawdown with holding the asset and holding the imported benchmark. Costs apply to every entry and exit, including final liquidation. Flat intervals are excluded from directional accuracy. Cash earns zero. A high hit rate can coexist with a large loss. The report is downloadable JSON, with dates, assumptions and signal information dates. Input changes invalidate the previous result.

This is a historical educational baseline, not a validated stock-selection system or buy/sell recommendation. It does not account for all slippage, taxes, spreads, execution constraints, changing universes, delistings or survivorship bias. Corporate-action adjustments depend on imported data. Repeatedly changing settings after looking at the test contaminates it: the last 30% is not independent evidence after inspection. Separate untouched periods, multiple assets, walk-forward evaluation and forward paper observation are needed before claiming predictive usefulness. No such predictive performance has been established for this release.

## Other calculations

Savings scenarios use month-end contributions and constant assumed annual return, annual fee and inflation. They permit negative returns. They are scenarios, not forecasts; taxes are excluded. The income calculator subtracts entered costs from revenue and divides by all entered hours, before tax. It does not validate an opportunity or customer.

All financial calculations are browser-local. The server serves static code and the source directory; there are no new network fetch endpoints, account connections, passwords, orders, claim submissions or background refresh jobs.
