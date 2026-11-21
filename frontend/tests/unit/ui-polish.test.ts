import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Button } from "../../src/components/ui/button";
import { PageHeader, Status } from "../../src/components/common";

const css = readFileSync(
  new URL("../../src/styles.css", import.meta.url),
  "utf8",
);
const token = (name: string) => {
  const value = css.match(new RegExp(`--color-${name}: (#[0-9a-f]{6});`))?.[1];
  if (!value) throw new Error(`Missing color token: ${name}`);
  return value;
};
const luminance = (color: string) =>
  [1, 3, 5]
    .map((start) => parseInt(color.slice(start, start + 2), 16) / 255)
    .map((value) =>
      value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
    )
    .reduce(
      (total, value, i) => total + value * [0.2126, 0.7152, 0.0722][i],
      0,
    );
const contrast = (a: string, b: string) => {
  const values = [luminance(a), luminance(b)].sort((x, y) => x - y);
  return (values[1] + 0.05) / (values[0] + 0.05);
};

describe("research workbench polish contracts", () => {
  it("keeps every explicit status label above normal-text contrast", () => {
    const rules = [...css.matchAll(/(\.status[^{}]*)\{([^{}]*)\}/g)];
    let checked = 0;
    for (const [, selector, body] of rules) {
      const background = body.match(/background: (#[0-9a-f]{6});/)?.[1];
      const color = body.match(/color: (#[0-9a-f]{6});/)?.[1];
      if (!background || !color) continue;
      expect(contrast(color, background), selector).toBeGreaterThanOrEqual(4.5);
      checked += 1;
    }
    expect(checked).toBeGreaterThanOrEqual(8);
  });
  it.each([
    ["foreground", "background"],
    ["muted-foreground", "background"],
    ["muted-foreground", "card"],
    ["primary-foreground", "primary"],
    ["secondary-foreground", "secondary"],
    ["accent-foreground", "accent"],
  ])("%s text keeps normal-text contrast on %s", (text, surface) => {
    expect(contrast(token(text), token(surface))).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps page identity and status readable without decorative icons", () => {
    const html = renderToStaticMarkup(
      createElement(PageHeader, {
        title: "Research overview",
        subtitle: "Inputs, experiments and evidence",
      }),
    );
    expect(html).toContain("Research workspace");
    expect(html).toContain("<h1>Research overview</h1>");
    const status = renderToStaticMarkup(
      createElement(Status, { status: "failed" }),
    );
    expect(status).toContain("Failed");
    expect(status).toContain('data-testid="run-status"');
  });

  it("retains button type, disabled state and a bounded color transition", () => {
    const html = renderToStaticMarkup(
      createElement(Button, { type: "submit", disabled: true }, "Submit"),
    );
    expect(html).toContain('type="submit"');
    expect(html).toContain('disabled=""');
    expect(html).toContain("transition-colors");
    expect(html).not.toContain("transition-all");
  });
});
