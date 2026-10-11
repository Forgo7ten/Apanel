import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { QueryClient } from "@tanstack/react-query";
import { queryKeys } from "../src/lib/query-keys.mjs";
import { buildColumnModeUpdate, columnModeFields, columnModeParameters } from "../src/lib/column-mode-contract.mjs";
import { watchRefreshInterval, WATCH_BOOTSTRAP_POLL_INTERVAL_MS, WATCH_DETAIL_REFRESH_INTERVAL_MS, WATCH_BOOTSTRAP_TIMEOUT_MS } from "../src/lib/watch-contract.mjs";
import { alertThresholdLabel, buildAlertPayload, displayAlertThreshold } from "../src/lib/notification-contract.mjs";

function sources(root) {
  return readdirSync(root, { withFileTypes: true }).flatMap((item) => {
    const path = join(root, item.name);
    return item.isDirectory() ? sources(path) : /\.(ts|tsx)$/.test(path) ? [path] : [];
  });
}

test("all TanStack query keys use one vocabulary and no orphaned detail key writes", () => {
  const root = fileURLToPath(new URL("../src/", import.meta.url));
  for (const filename of sources(root)) {
    const source = readFileSync(filename, "utf8");
    assert.doesNotMatch(source, /queryKey:\s*\[/, `${filename} has a literal query key`);
    assert.doesNotMatch(source, /setQueryData(?:<[^>]+>)?\s*\(\s*\[\s*["']watch-table["']/, `${filename} writes the wrong watch-detail key`);
    assert.doesNotMatch(source, /["']watch-table-columns["']/, `${filename} invalidates a non-existent query`);
  }
  assert.deepEqual(queryKeys.watchTable(7, "qfq"), ["watch-table", 7, "qfq"]);
  assert.deepEqual(queryKeys.watchTable(7, "none"), ["watch-table", 7, "none"]);
  assert.deepEqual(queryKeys.securitySearch(" 600519 "), queryKeys.securitySearch("600519"));
});

test("watch mutations reach all adjustments but not another user's separate table", () => {
  const client = new QueryClient();
  client.setQueryData(queryKeys.watchTable(3, "qfq"), { id: 3, stocks: [1] });
  client.setQueryData(queryKeys.watchTable(3, "none"), { id: 3, stocks: [1] });
  client.setQueryData(queryKeys.watchTable(4, "qfq"), { id: 4, stocks: [9] });
  const updated = client.setQueriesData({ queryKey: queryKeys.watchTablePrefix(3) }, (current) => ({ ...current, stocks: [] }));
  assert.equal(updated.length, 2);
  assert.deepEqual(client.getQueryData(queryKeys.watchTable(3, "qfq")).stocks, []);
  assert.deepEqual(client.getQueryData(queryKeys.watchTable(3, "none")).stocks, []);
  assert.deepEqual(client.getQueryData(queryKeys.watchTable(4, "qfq")).stocks, [9]);
  assert.equal(client.getQueryData(queryKeys.watchTablePrefix(3)), undefined, "no phantom prefix cache");
});

test("column mode transitions validate fields, state parameters and clear old identity", () => {
  const base = { indicator_type: "BOLL", view_mode: "COMPOSITE", parameters: { period: 20, multiplier: 2 } };
  assert.deepEqual(columnModeParameters(base, "NUMBER"), { period: 20, multiplier: 2 });
  assert.deepEqual(buildColumnModeUpdate({ indicatorType: "BOLL", viewMode: "NUMBER", parameters: base.parameters, field: "upper" }), {
    view_mode: "NUMBER", state_code: null, parameters: { period: 20, multiplier: 2, field: "upper" },
  });
  assert.throws(() => buildColumnModeUpdate({ indicatorType: "BOLL", viewMode: "NUMBER", parameters: base.parameters }), /字段/);
  assert.deepEqual(columnModeParameters({ ...base, view_mode: "STATUS", parameters: { period: 9 } }, "NUMBER"), { period: 9 });
  assert.deepEqual(columnModeParameters({ ...base, view_mode: "COMPOSITE" }, "STATUS"), { period: 20, multiplier: 2 });
  assert.deepEqual(columnModeParameters({ indicator_type: "MA", view_mode: "COMPOSITE", parameters: { periods: [5, 10] } }, "STATUS"), { short_period: 5, long_period: 10 });
  assert.deepEqual(columnModeFields("MA", { periods: [5, 10] }).map((option) => option.value), ["ma5", "ma10"]);
  assert.throws(() => buildColumnModeUpdate({ indicatorType: "MA", viewMode: "STATUS", parameters: { short_period: 10, long_period: 5 }, stateCode: "MA_CROSS_UP" }), /短周期/);
  assert.deepEqual(buildColumnModeUpdate({ indicatorType: "MA", viewMode: "STATUS", parameters: { short_period: 5, long_period: 10 }, stateCode: "MA_CROSS_UP" }), {
    view_mode: "STATUS", state_code: "MA_CROSS_UP", parameters: { short_period: 5, long_period: 10 },
  });
  assert.throws(() => buildColumnModeUpdate({ indicatorType: "MA", viewMode: "NUMBER", parameters: { periods: [5, 10] }, field: "ma20" }), /字段/);
  assert.throws(() => buildColumnModeUpdate({ indicatorType: "RSI", viewMode: "NUMBER", parameters: { period: 2.5 } }), /整数/);
});

test("dividend alerts roundtrip a 4-percent display through decimal thresholds", () => {
  for (const input of ["0", "4", "4.5"]) {
    const saved = buildAlertPayload({ security_id: 11, condition_type: "VALUE", indicator: "DIVIDEND_YIELD", operator: ">=", threshold: input });
    assert.equal(saved.threshold, Number(input) / 100);
    assert.equal(displayAlertThreshold("DIVIDEND_YIELD", saved.threshold), input);
    assert.equal(alertThresholdLabel("DIVIDEND_YIELD", saved.threshold), `${input}%`);
  }
  assert.equal(displayAlertThreshold("DIVIDEND_YIELD", undefined), "4");
  assert.equal(buildAlertPayload({ security_id: 11, condition_type: "VALUE", indicator: "RSI", operator: ">=", threshold: "70" }).threshold, 70);
});

test("bootstrap polls from backend readiness with a fixed deadline and recovers after reload", () => {
  const observed = new Map();
  const pending = { id: 7, adjust_type: "qfq", columns: [], stocks: [{ security_id: 42, bootstrap_ready: false }] };
  assert.equal(watchRefreshInterval(pending, observed, 1000), WATCH_BOOTSTRAP_POLL_INTERVAL_MS);
  assert.equal(observed.get("7:qfq:42"), 1000);
  assert.equal(watchRefreshInterval(pending, observed, 1000 + WATCH_BOOTSTRAP_TIMEOUT_MS), WATCH_DETAIL_REFRESH_INTERVAL_MS);
  const ready = { ...pending, stocks: [{ security_id: 42, bootstrap_ready: true }] };
  assert.equal(watchRefreshInterval(ready, observed, 1000 + WATCH_BOOTSTRAP_TIMEOUT_MS), WATCH_DETAIL_REFRESH_INTERVAL_MS);
  assert.equal(observed.has("7:qfq:42"), false);
  assert.equal(watchRefreshInterval(pending, observed, 120_000), WATCH_BOOTSTRAP_POLL_INTERVAL_MS);
  assert.equal(watchRefreshInterval({ ...pending, adjust_type: "none" }, observed, 120_001), WATCH_BOOTSTRAP_POLL_INTERVAL_MS);
});

test("notification history uses an infinite query and user-controlled refresh", () => {
  const source = readFileSync(new URL("../src/app/(workspace)/notifications/NotificationsWorkspace.tsx", import.meta.url), "utf8");
  assert.match(source, /useInfiniteQuery\(/);
  assert.match(source, /getNextPageParam/);
  assert.match(source, /refetchInterval: 60_000/);
  assert.match(source, /加载更早通知/);
  assert.match(source, /notificationsQuery\.refetch\(\)/);
});

test("confirm-delete, drag MIME payload, fixed header and expanded states have interaction paths", () => {
  const watch = readFileSync(new URL("../src/app/(workspace)/watch/page.tsx", import.meta.url), "utf8");
  const table = readFileSync(new URL("../src/components/table/WatchTable.tsx", import.meta.url), "utf8");
  const wizard = readFileSync(new URL("../src/components/alerts/AlertWizard.tsx", import.meta.url), "utf8");
  assert.match(watch, /window\.confirm\(`确定删除监控表/);
  assert.match(table, /dataTransfer\.setData\("text\/plain"/);
  assert.match(table, /fixedHeader \? \(/);
  assert.match(table, /展开其余.*个状态/);
  assert.match(wizard, /createPortal\(/);
  assert.match(wizard, /trapDialogTab\(/);
  assert.match(wizard, /stopImmediatePropagation\(\)/);
});

test("custom indicator mode transitions retain exact periods and multipliers", async () => {
  const { columnModeParameters, maModeNeedsConfirmation } = await import("../src/lib/column-mode-contract.mjs");
  for (const [indicator_type, parameters] of [
    ["BOLL", { period: 30, multiplier: 2.5 }],
    ["MACD", { fast_period: 6, slow_period: 13, signal_period: 5 }],
    ["RSI", { period: 21 }],
    ["KDJ", { period: 14, k_period: 4, d_period: 3 }],
  ]) for (const from of ["COMPOSITE", "STATUS", "NUMBER"]) for (const to of ["COMPOSITE", "STATUS", "NUMBER", "DELTA"]) {
    assert.deepEqual(columnModeParameters({ indicator_type, view_mode: from, parameters: { ...parameters, field: "value" } }, to), parameters);
  }
  assert.deepEqual(columnModeParameters({ indicator_type: "MA", view_mode: "STATUS", parameters: { short_period: 20, long_period: 60 } }, "NUMBER"), { periods: [20, 60] });
  assert.deepEqual(columnModeParameters({ indicator_type: "MA", view_mode: "NUMBER", parameters: { periods: [20, 60] } }, "STATUS"), { short_period: 20, long_period: 60 });
  assert.equal(maModeNeedsConfirmation({ indicator_type: "MA", view_mode: "NUMBER", parameters: { periods: [5, 10, 30] } }, "STATUS"), true);
});

test("state column matching uses exact indicator identity and parameters", async () => {
  const { stateMatchesIndicatorColumn, selectColumnStateHistory } = await import("../src/lib/state-column-contract.mjs");
  const state = { indicator_type: "BOLL", state_code: "BOLL_WIDTH_NARROWING", parameters: { period: 30, multiplier: 2.5 } };
  const matching = { indicator_type: "BOLL", view_mode: "COMPOSITE", parameters: { period: 30, multiplier: 2.5 } };
  assert.equal(stateMatchesIndicatorColumn(state, matching), true);
  assert.equal(stateMatchesIndicatorColumn(state, { ...matching, parameters: { period: 20, multiplier: 2 } }), false);
  assert.equal(stateMatchesIndicatorColumn(state, { ...matching, indicator_type: "MACD" }), false);
  assert.deepEqual(selectColumnStateHistory([state], matching), [state]);
  assert.deepEqual(selectColumnStateHistory([state], { ...matching, parameters: { period: 20, multiplier: 2 } }), []);
});
