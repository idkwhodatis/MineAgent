/** Presentation only; origin is supplied by the owner-checked server projection. */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import { InputOriginGroups } from "../../src/features/engine-inputs";
import { groupInputs } from "../../src/lib/engine-inputs";
import type {
  ResearchInputItem,
  ResearchInputs,
} from "../../src/client/types.gen";
const item = (
  origin: ResearchInputItem["origin"],
  id: string,
): ResearchInputItem => ({
  id,
  origin,
  default_pinned: origin === "user_provided",
  created_at: "2024-01-01T00:00:00Z",
  title: `${origin} ${id}`,
  provenance:
    origin === "user_provided"
      ? "authenticated_submission_receipt"
      : origin === "agent_generated"
        ? "model_response_source_witness"
        : "origin_not_established",
  content_sha256: "a".repeat(64),
  operations: [],
});
const values = [
  item("unknown", "u"),
  item("agent_generated", "b"),
  item("user_provided", "c"),
  item("agent_generated", "a"),
];
const data: ResearchInputs = {
  run_id: "run",
  items: values,
  has_more_proposals: false,
};
it("pins user declarations first and uses a deterministic timestamp/id order", () => {
  expect(
    groupInputs(values).map((group) => [
      group.origin,
      ...group.items.map((row) => row.id),
    ]),
  ).toEqual([
    ["user_provided", "c"],
    ["agent_generated", "a", "b"],
    ["unknown", "u"],
  ]);
  expect(groupInputs([...values].reverse())).toEqual(groupInputs(values));
});
it("refresh reconstructs exactly the same grouping without modifying requests or approval state", () => {
  const before = JSON.stringify(data);
  const render = (value: ResearchInputs) =>
    renderToStaticMarkup(createElement(InputOriginGroups, { data: value }));
  expect(render(data)).toBe(render(JSON.parse(before)));
  expect(JSON.stringify(data)).toBe(before);
  const html = render(data);
  expect(html.indexOf("User-provided submissions")).toBeLessThan(
    html.indexOf("Agent-generated proposals"),
  );
  expect(html).toContain("Pinned user input");
  expect(html).toContain("not human approval");
  expect(html).toContain("not machine-learning supervision");
});
it("unknown origin never receives a user pin or fabricated declaration", () => {
  const html = renderToStaticMarkup(
    createElement(InputOriginGroups, {
      data: { ...data, items: [item("unknown", "old")] },
    }),
  );
  expect(html).not.toContain('aria-label="Pinned user input"');
  expect(html).not.toContain("Exact recorded submission declarations");
  expect(html).toContain("Origin not established");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});
it("exact proposed factor navigation retains SUBPATH without release or approval actions", async () => {
  vi.resetModules();
  vi.stubGlobal("document", {
    querySelector: () => ({ content: "/research" }),
  });
  const { InputOriginGroups: Prefixed } =
    await import("../../src/features/engine-inputs");
  const html = renderToStaticMarkup(
    createElement(Prefixed, {
      data: {
        ...data,
        items: [
          {
            ...item("agent_generated", "proposal"),
            factor_id: "factor",
            factor_version_id: "version",
          },
        ],
      },
    }),
  );
  expect(html).toContain("/research/factors/factor/versions/version");
  expect(html).not.toContain("<button");
});

it("shows frozen steer versions in accepted order, never current head or compliance", () => {
  const row: ResearchInputItem = {
    ...item("user_provided", "accepted"),
    steer_snapshot: {
      owner_id: "owner",
      versions: [
        {
          steer_id: "s2",
          version_id: "old-v2",
          ordinal: 2,
          kind: "constraints",
          text: "Original constraint",
          text_sha256: "b".repeat(64),
        },
        {
          steer_id: "s1",
          version_id: "old-v1",
          ordinal: 1,
          kind: "hypothesis",
          text: "Original hypothesis",
          text_sha256: "c".repeat(64),
        },
      ],
    },
    steer_snapshot_sha256: "d".repeat(64),
    model_binding_sha256: "e".repeat(64),
    steer_evidence: "accepted_input_snapshot_not_execution_or_compliance",
  };
  const value = { ...data, items: [row] };
  const html = renderToStaticMarkup(
    createElement(InputOriginGroups, { data: value }),
  );
  expect(html.indexOf("Original constraint")).toBeLessThan(
    html.indexOf("Original hypothesis"),
  );
  expect(html).toContain(
    "Later edits or disabling a steer do not replace these inputs",
  );
  expect(html).toContain("not proof that a model");
  expect(html).toContain("guidance, not verified platform rules");
  expect(html).toContain("d".repeat(64));
  expect(html).toBe(
    renderToStaticMarkup(
      createElement(InputOriginGroups, {
        data: JSON.parse(JSON.stringify(value)),
      }),
    ),
  );
});
it("does not present a stray snapshot as authenticated user guidance", () => {
  const html = renderToStaticMarkup(
    createElement(InputOriginGroups, {
      data: {
        ...data,
        items: [
          {
            ...item("unknown", "old"),
            steer_snapshot: { owner_id: "owner", versions: [] },
          },
        ],
      },
    }),
  );
  expect(html).not.toContain("Frozen submission steer inputs");
});
