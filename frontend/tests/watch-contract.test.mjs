import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  getDefaultIndicatorParameters,
  getIndicatorFieldOptions,
  getSecurityIdentifier,
  toAddStockPayload,
  toCreateColumnPayload,
  toColumnOrderPayload,
  errorBoundaryCopy,
  calculateVirtualRange,
  dialogFocusTargetIndex,
  isCurrentWatchDetail,
  virtualScrollTopForKey,
  watchRowLayoutContract,
  watchDetailViewState,
} from "../src/lib/watch-contract.mjs";

test("uses the server-provided security identifier without deriving one from symbol", () => {
  assert.equal(getSecurityIdentifier({ symbol: "600519", name: "贵州茅台" }), null);
  assert.deepEqual(toAddStockPayload({ symbol: "600519", id: 42 }), { security_id: 42 });
  assert.deepEqual(toAddStockPayload({ symbol: "600519", security_id: "42" }), { security_id: "42" });
});

test("rejects empty and non-finite identifiers", () => {
  assert.equal(getSecurityIdentifier({ id: "  " }), null);
  assert.equal(getSecurityIdentifier({ id: Number.NaN }), null);
  assert.equal(toAddStockPayload(null), null);
});

test("indicator defaults use the persistence parameter vocabulary", () => {
  assert.deepEqual(getDefaultIndicatorParameters("MA"), { period: 5 });
  assert.deepEqual(getDefaultIndicatorParameters("RSI"), { period: 14 });
  assert.deepEqual(getDefaultIndicatorParameters("BOLL"), { period: 20, multiplier: 2 });
  assert.deepEqual(getDefaultIndicatorParameters("KDJ"), { period: 9, k_period: 3, d_period: 3 });
  assert.deepEqual(getDefaultIndicatorParameters("MACD"), { fast_period: 12, slow_period: 26, signal_period: 9 });
  assert.deepEqual(getDefaultIndicatorParameters("DIVIDEND_YIELD"), {});
});

test("column payload preserves selected mode and strict indicator parameters", () => {
  assert.deepEqual(
    toCreateColumnPayload({ indicatorType: "boll", viewMode: "COMPOSITE" }),
    {
      column_type: "INDICATOR",
      indicator_type: "BOLL",
      parameters: { period: 20, multiplier: 2 },
      view_mode: "COMPOSITE",
    },
  );
  assert.deepEqual(
    toCreateColumnPayload({ indicatorType: "MA", viewMode: "NUMBER", parameters: { period: 20 } }),
    {
      column_type: "INDICATOR",
      indicator_type: "MA",
      parameters: { period: 20 },
      view_mode: "NUMBER",
    },
  );
  assert.deepEqual(
    toCreateColumnPayload({
      indicatorType: "BOLL",
      viewMode: "NUMBER",
      parameters: { period: 20, multiplier: 2, field: "upper" },
    }),
    {
      column_type: "INDICATOR",
      indicator_type: "BOLL",
      parameters: { period: 20, multiplier: 2, field: "upper" },
      view_mode: "NUMBER",
    },
  );
  assert.deepEqual(
    toCreateColumnPayload({
      indicatorType: "MACD",
      viewMode: "COMPOSITE",
      parameters: { fast_period: 12, slow_period: 26, signal_period: 9, field: "diff" },
    }).parameters,
    { fast_period: 12, slow_period: 26, signal_period: 9 },
  );
});

test("composite indicator field options use the canonical backend vocabulary", () => {
  assert.deepEqual(getIndicatorFieldOptions("BOLL").map((option) => option.value), ["upper", "middle", "lower"]);
  assert.deepEqual(getIndicatorFieldOptions("KDJ").map((option) => option.value), ["k", "d", "j"]);
  assert.deepEqual(getIndicatorFieldOptions("MACD").map((option) => option.value), ["diff", "dea", "histogram"]);
  assert.deepEqual(getIndicatorFieldOptions("MA"), []);
});

test("column order payload only contains persisted dynamic column ids", () => {
  assert.deepEqual(toColumnOrderPayload(["security", "price", "14", "7", "states"]), {
    column_ids: [14, 7],
  });
  assert.deepEqual(toColumnOrderPayload(["security", "price", "states"]), { column_ids: [] });
});

test("watch detail keeps stale data during refresh and separates initial errors", () => {
  assert.equal(watchDetailViewState({ isPending: true }), "loading");
  assert.equal(watchDetailViewState({ isError: true }), "error");
  assert.equal(watchDetailViewState({ hasData: true }), "ready");
  assert.equal(watchDetailViewState({ hasData: true, isFetching: true }), "refreshing");
  assert.equal(watchDetailViewState({ hasData: true, isError: true }), "refresh-error");
  assert.equal(watchDetailViewState({}), "empty");
});

test("detail data is reusable only for the active watch table", () => {
  assert.equal(isCurrentWatchDetail({ id: 7 }, 7), true);
  assert.equal(isCurrentWatchDetail({ id: "7" }, 7), true);
  assert.equal(isCurrentWatchDetail({ id: 8 }, 7), false);
  assert.equal(isCurrentWatchDetail(null, 7), false);
  assert.equal(isCurrentWatchDetail({ id: 7 }, null), false);
});

test("virtual range handles empty, small, scrolling, and boundary rows", () => {
  assert.deepEqual(calculateVirtualRange({ itemCount: 0, viewportHeight: 400 }), {
    start: 0,
    end: 0,
    offsetTop: 0,
    offsetBottom: 0,
    totalHeight: 0,
  });
  assert.deepEqual(calculateVirtualRange({ itemCount: 4, viewportHeight: 200, rowHeight: 50, overscan: 2 }), {
    start: 0,
    end: 4,
    offsetTop: 0,
    offsetBottom: 0,
    totalHeight: 200,
  });
  const initial = calculateVirtualRange({ itemCount: 100, viewportHeight: 200, rowHeight: 50, overscan: 2 });
  assert.deepEqual(initial, { start: 0, end: 8, offsetTop: 0, offsetBottom: 4600, totalHeight: 5000 });
  const middle = calculateVirtualRange({ itemCount: 100, scrollTop: 2500, viewportHeight: 200, rowHeight: 50, overscan: 2 });
  assert.deepEqual(middle, { start: 48, end: 56, offsetTop: 2400, offsetBottom: 2200, totalHeight: 5000 });
  const bottom = calculateVirtualRange({ itemCount: 100, scrollTop: 99999, viewportHeight: 200, rowHeight: 50, overscan: 2 });
  assert.deepEqual(bottom, { start: 94, end: 100, offsetTop: 4700, offsetBottom: 0, totalHeight: 5000 });
});

test("dialog focus target wraps only at the tab boundaries", () => {
  assert.equal(dialogFocusTargetIndex(-1, 3, false), 0);
  assert.equal(dialogFocusTargetIndex(-1, 3, true), 2);
  assert.equal(dialogFocusTargetIndex(0, 3, true), 2);
  assert.equal(dialogFocusTargetIndex(2, 3, false), 0);
  assert.equal(dialogFocusTargetIndex(1, 3, false), -1);
  assert.equal(dialogFocusTargetIndex(0, 0, false), -1);
});

test("dynamic watch rows contain every composite field and fit their state layout", () => {
  const fieldCounts = [0, 3, 4, 5, 12];
  const layouts = fieldCounts.map((fieldCount) => watchRowLayoutContract(fieldCount));

  assert.equal(layouts[0].fieldCount, 0);
  assert.equal(layouts[0].effectiveFieldCount, 1);
  assert.equal(layouts[0].rowHeight, 72);
  assert.equal(layouts[1].contentHeight, 132);
  assert.equal(layouts[2].contentHeight, 166);
  assert.equal(layouts[3].contentHeight, 200);
  assert.equal(layouts[4].fieldCount, 12);
  assert.ok(layouts.every((layout) => layout.contentHeight <= layout.rowHeight));
  assert.ok(layouts.every((layout, index) => index === 0 || layout.rowHeight >= layouts[index - 1].rowHeight));
  assert.equal(layouts[4].rowHeight, 440);
  assert.equal(layouts[4].maxStateTags, 2);
});

test("virtual list keyboard commands return bounded row or page destinations", () => {
  const rowHeight = watchRowLayoutContract(5).rowHeight;
  assert.equal(rowHeight, 200);
  assert.equal(virtualScrollTopForKey("ArrowDown", 72, 560, 2000, rowHeight), 272);
  assert.equal(virtualScrollTopForKey("ArrowUp", 72, 560, 2000, rowHeight), 0);
  assert.equal(virtualScrollTopForKey("PageDown", 560, 560, 2000, rowHeight), 1120);
  assert.equal(virtualScrollTopForKey("PageUp", 560, 560, 2000, rowHeight), 0);
  assert.equal(virtualScrollTopForKey("Home", 800, 560, 2000, rowHeight), 0);
  assert.equal(virtualScrollTopForKey("End", 800, 560, 2000, rowHeight), 1440);
  assert.equal(virtualScrollTopForKey("Enter", 800, 560, 2000, rowHeight), null);
});

test("error boundary copy is safe and exposes a retry contract", () => {
  assert.deepEqual(errorBoundaryCopy(), {
    title: "页面暂时不可用",
    description: "页面加载遇到问题，请重试。",
    retryLabel: "重新加载",
  });
  const workspaceBoundary = readFileSync(new URL("../src/app/(workspace)/error.tsx", import.meta.url), "utf8");
  const globalBoundary = readFileSync(new URL("../src/app/global-error.tsx", import.meta.url), "utf8");
  assert.match(workspaceBoundary, /onRetry=\{reset\}/);
  assert.match(globalBoundary, /onRetry=\{reset\}/);
});

test("watch table renders all rows from the detail payload without cell requests", () => {
  const source = readFileSync(new URL("../src/components/table/WatchTable.tsx", import.meta.url), "utf8");
  assert.match(source, /data: table\.stocks \?\? \[\]/);
  assert.doesNotMatch(source, /getIndicatorHistory|getStateHistory|fetch\(/);
});

test("watch page remounts the table only when its persisted table id changes", () => {
  const source = readFileSync(new URL("../src/app/(workspace)/watch/page.tsx", import.meta.url), "utf8");
  assert.match(source, /<WatchTable\s+key=\{String\(currentDetail\.id \?\? activeTableId\)\}/);
  assert.doesNotMatch(source, /<WatchTable\s+key=\{currentDetail\}/);
});
