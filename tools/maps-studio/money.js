'use strict';

// Deterministic research calculations. No orders, network, storage or quote feed.
const MoneyTools = (() => {
  function number(value, min, max, label) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max) throw Error('Check ' + label + '.');
    return value;
  }
  function projection({initial, monthly, years, annual, fee, inflation}) {
    number(initial, 0, 1e8, 'starting balance'); number(monthly, 0, 1e6, 'monthly contribution');
    number(years, 1, 60, 'whole years'); if (!Number.isInteger(years)) throw Error('Use whole years.');
    number(annual, -90, 50, 'assumed annual return'); number(fee, 0, 10, 'annual fee'); number(inflation, -5, 30, 'inflation');
    const net = (1 + annual / 100) * (1 - fee / 100) - 1;
    const rate = (1 + net) ** (1 / 12) - 1;
    let value = initial;
    const rows = [];
    for (let month = 1; month <= years * 12; month++) {
      value = value * (1 + rate) + monthly;
      if (month % 12 === 0) rows.push({year: month / 12, nominal: value, contributed: initial + monthly * month,
        purchasingPower: value / (1 + inflation / 100) ** (month / 12)});
    }
    return {assumptions: {initial, monthly, years, annual, fee, inflation}, rows,
      note: 'Hypothetical constant returns; contributions at month end. Fees are approximated annually; tax is excluded. This is not a forecast.'};
  }
  function parsePrices(text, today = new Date().toISOString().slice(0, 10)) {
    if (typeof text !== 'string' || text.length > 500000) throw Error('Choose a CSV under 500 KB.');
    const lines = text.replace(/^\uFEFF/, '').trim().split(/\r?\n/);
    if (lines.shift()?.trim().toLowerCase() !== 'date,asset,benchmark') throw Error('CSV header must be date,asset,benchmark. Use aligned daily adjusted closing values or total-return indices.');
    if (lines.length < 70 || lines.length > 5000) throw Error('Use 70–5,000 aligned daily observations.');
    let previous = '';
    return lines.map((line, index) => {
      const parts = line.split(',').map(x => x.trim());
      if (parts.length !== 3 || !/^\d{4}-\d{2}-\d{2}$/.test(parts[0])) throw Error('Invalid CSV row ' + (index + 2));
      const [date, a, b] = parts;
      const stamp = Date.parse(date + 'T00:00:00Z');
      if (!Number.isFinite(stamp) || new Date(stamp).toISOString().slice(0, 10) !== date || date <= previous || date > today) throw Error('Dates must be valid, unique, increasing and not in the future.');
      previous = date;
      if (!/^\d+(?:\.\d+)?$/.test(a) || !/^\d+(?:\.\d+)?$/.test(b)) throw Error('Prices must be positive decimal values, without formulas or missing entries.');
      return {date, asset: number(Number(a), 1e-8, 1e12, 'asset value'), benchmark: number(Number(b), 1e-8, 1e12, 'benchmark value')};
    });
  }
  function metrics(values) {
    let peak = values[0], worst = 0;
    for (const value of values) {
      if (!Number.isFinite(value) || value <= 0) throw Error('Values overflowed. Check the price series.');
      peak = Math.max(peak, value); worst = Math.min(worst, value / peak - 1);
    }
    return {returnPct: (values.at(-1) / values[0] - 1) * 100, maxDrawdownPct: -worst * 100, endingPer100: values.at(-1) * 100};
  }
  function simulate(rows, window, costBps) {
    number(window, 2, 252, 'moving-average window'); if (!Number.isInteger(window)) throw Error('Use a whole window size.');
    number(costBps, 0, 500, 'cost per side');
    const start = Math.max(window + 1, Math.floor(rows.length * 0.7));
    if (rows.length - start - 1 < 20) throw Error('Need at least 20 test intervals after the initial 70% and indicator warm-up. Add more history or reduce the window.');
    let wealth = 1, position = 0, correct = 0, classified = 0, up = 0, trades = 0;
    const curve = [1], hold = [1], benchmark = [1], signals = [];
    const cost = costBps / 10000;
    for (let i = start; i < rows.length - 1; i++) {
      // Only data THROUGH YESTERDAY affects the position entered at today's close.
      // This extra observation delay avoids trading on an as-yet-unknown close.
      let sum = 0;
      for (let j = i - window; j < i; j++) sum += rows[j].asset;
      const next = rows[i - 1].asset > sum / window ? 1 : 0;
      const change = rows[i + 1].asset / rows[i].asset - 1;
      if (next !== position) { wealth *= 1 - cost; trades++; }
      wealth *= 1 + next * change;
      position = next;
      if (change !== 0) { classified++; if (change > 0) up++; if ((change > 0) === !!next) correct++; }
      signals.push({executionDate: rows[i].date, informationThrough: rows[i - 1].date, exposure: next});
      curve.push(wealth);
      hold.push(rows[i + 1].asset / rows[start].asset * (1 - cost));
      benchmark.push(rows[i + 1].benchmark / rows[start].benchmark * (1 - cost));
    }
    if (position) { wealth *= 1 - cost; trades++; curve[curve.length - 1] = wealth; }
    hold[hold.length - 1] *= 1 - cost; benchmark[benchmark.length - 1] *= 1 - cost;
    return {algorithm: 'Lagged moving-average trend, long or cash; fixed chronological 70/30 split',
      window, costBps, testStart: rows[start].date, testEnd: rows.at(-1).date,
      observations: rows.length, testIntervals: rows.length - start - 1, classifiedIntervals: classified,
      directionAccuracyPct: classified ? correct / classified * 100 : null,
      alwaysUpAccuracyPct: classified ? up / classified * 100 : null,
      strategy: metrics(curve), assetHold: metrics(hold), benchmarkHold: metrics(benchmark), trades, signals,
      note: 'Historical demonstration, not validated stock-selection advice. No parameters are fitted. Last 30% is a chronological test segment, not an independent holdout after you inspect or tune it. Cash earns zero; costs apply per entry/exit, including final liquidation. Taxes, spreads beyond the entered cost, execution failures, delistings and survivorship bias are not modeled. Imported data quality and corporate-action adjustments are your responsibility. No live quotes or orders.'};
  }
  function opportunity(revenue, costs, hours) {
    number(revenue, 0, 1e9, 'revenue'); number(costs, 0, 1e9, 'all costs'); number(hours, 0.01, 100000, 'total hours');
    return {netBeforeTax: revenue - costs, hourlyBeforeTax: (revenue - costs) / hours};
  }
  return {projection, parsePrices, simulate, opportunity};
})();
if (typeof module !== 'undefined') module.exports = MoneyTools;
