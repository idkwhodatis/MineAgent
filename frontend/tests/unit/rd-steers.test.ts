import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import {
  SteerVersionSelection,
  STEER_QUALIFICATION,
} from "../../src/features/rd-steers";
import {
  appendSteerSelection,
  moveSteerSelection,
} from "../../src/lib/rd-steers";
import type { SteerView } from "../../src/lib/rd-steers";
const row: SteerView = {
  id: "root",
  revision: 2,
  disabled: false,
  created_at: "2024-01-01",
  updated_at: "2024-01-02",
  current: {
    schema_version: "mineagent.rdagent-steer-version.v1",
    steer_id: "root",
    version_id: "current",
    ordinal: 2,
    kind: "hypothesis",
    text: "Test a hypothesis",
    text_sha256: "a".repeat(64),
  },
};
afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});
it("preserves explicit ordering and never mutates a frozen selection", () => {
  const original = ["old", "current"];
  expect(moveSteerSelection(original, 1, -1)).toEqual(["current", "old"]);
  expect(original).toEqual(["old", "current"]);
  expect(appendSteerSelection(original, "third")).toEqual([
    "old",
    "current",
    "third",
  ]);
  expect(appendSteerSelection(original, "old")).toBe(original);
  expect(() =>
    appendSteerSelection(
      Array.from({ length: 8 }, (_, i) => String(i)),
      "ninth",
    ),
  ).toThrow("at most eight");
});
it("changed and disabled selected versions stay explicit, never silently upgraded", () => {
  const html = renderToStaticMarkup(
    createElement(SteerVersionSelection, {
      rows: [
        row,
        {
          ...row,
          id: "disabled",
          disabled: true,
          current: { ...row.current, version_id: "disabled" },
        },
      ],
      selected: ["old", "disabled"],
      onChange: () => {},
    }),
  );
  expect(html).toContain("Selected version changed or unavailable: old");
  expect(html).toContain("Selected version changed or unavailable: disabled");
  expect(html).toContain("not be silently replaced");
  expect(html).not.toContain('checked=""');
});
it.each(["", "/research"])(
  "management link preserves deployment prefix %s",
  async (prefix) => {
    vi.resetModules();
    vi.stubGlobal("document", { querySelector: () => ({ content: prefix }) });
    const { SteerVersionSelection: Prefixed } =
      await import("../../src/features/rd-steers");
    const html = renderToStaticMarkup(
      createElement(Prefixed, {
        rows: [row],
        selected: ["current"],
        onChange: () => {},
      }),
    );
    expect(html).toContain(`href="${prefix}/rd-steers"`);
    expect(html).toContain("not verified model compliance");
    expect(html).toContain("not enforced platform rules");
    expect(html).toContain("cancel a run separately");
  },
);
it("qualifies future-only edits without promising human approval", () => {
  expect(STEER_QUALIFICATION).toContain("accepted run’s frozen input");
  expect(STEER_QUALIFICATION).not.toContain("approved");
});
