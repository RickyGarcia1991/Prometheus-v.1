'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const m = require('./money.js');
const csv = fn => 'date,asset,benchmark\n' + Array.from({length: 100}, (_, i) => {
  const date = new Date(Date.UTC(2020, 0, 1 + i)).toISOString().slice(0, 10);
  return `${date},${fn(i)},100`;
}).join('\n');
test('zero-return contributions and negative income opportunity', () => {
  const x = m.projection({initial: 1000, monthly: 100, years: 2, annual: 0, fee: 0, inflation: 0});
  assert.equal(x.rows[1].nominal, 3400); assert.equal(x.rows[1].contributed, 3400);
  assert.equal(m.opportunity(50, 70, 2).hourlyBeforeTax, -10);
});
test('annual fee and inflation have independent expected results', () => {
  const x = m.projection({initial: 1000, monthly: 0, years: 1, annual: 10, fee: 1, inflation: 10}).rows[0];
  assert.ok(Math.abs(x.nominal - 1089) < 1e-8); assert.ok(Math.abs(x.purchasingPower - 990) < 1e-8);
});
test('rising history equals buy-and-hold, including both costs', () => {
  const result = m.simulate(m.parsePrices(csv(i => 100 + i)), 20, 100);
  assert.equal(result.testIntervals, 29); assert.equal(result.directionAccuracyPct, 100);
  assert.equal(result.alwaysUpAccuracyPct, 100); assert.equal(result.trades, 2);
  assert.ok(Math.abs(result.strategy.endingPer100 - 199 / 170 * .99 * .99 * 100) < 1e-9);
  assert.ok(Math.abs(result.strategy.endingPer100 - result.assetHold.endingPer100) < 1e-9);
});
test('falling history stays cash; reported drawdown of hold is positive', () => {
  const result = m.simulate(m.parsePrices(csv(i => 200 - i)), 20, 0);
  assert.equal(result.directionAccuracyPct, 100); assert.equal(result.alwaysUpAccuracyPct, 0);
  assert.equal(result.strategy.endingPer100, 100); assert.equal(result.trades, 0);
  assert.ok(Math.abs(result.assetHold.maxDrawdownPct - 29 / 130 * 100) < 1e-9);
});
test('future changes cannot alter earlier signals; current close also cannot', () => {
  const rows = m.parsePrices(csv(i => 100 + i));
  const old = m.simulate(rows, 20, 10);
  const changed = rows.map((x, i) => i >= 80 ? {...x, asset: 1} : x);
  const next = m.simulate(changed, 20, 10);
  assert.deepEqual(old.signals.slice(0, 11), next.signals.slice(0, 11));
  assert.notDeepEqual(old.signals.slice(11), next.signals.slice(11));
});
test('flat prices have no fabricated directional accuracy', () => {
  const result = m.simulate(m.parsePrices(csv(() => 100)), 20, 0);
  assert.equal(result.directionAccuracyPct, null); assert.equal(result.classifiedIntervals, 0);
});
test('rejects missing, duplicated, future, invalid and spreadsheet formula values', () => {
  const text = csv(i => 100 + i);
  for (const bad of [text.replace('2020-01-02', '2020-01-01'), text.replace('2020-01-01', '2020-02-30'),
    text.replace('2020-01-01', '2099-01-01'), text.replace(',100,100', ',,100'), text.replace(',100,100', ',=1+1,100'),
    text.replace(',100,100', ',0,100')]) assert.throws(() => m.parsePrices(bad));
  assert.throws(() => m.simulate(m.parsePrices(text), 252, 0));
  assert.throws(() => m.opportunity(Infinity, 1, 1));
});
