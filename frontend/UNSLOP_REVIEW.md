# MineAgent WebUI polish

## Direction and scope

This pass applies the approved restrained, precise, traceable research-workbench
direction to the shared shell, overview and shared UI styles. The existing deep
green action color is retained. Features, routes, data contracts, module order,
research disclosures and trading/backend behavior are unchanged.

Based on master `860cd536b19098dfc5b3abdfdb7b481c55b1138e`.
Local implementation branch: `feat/unslop-webui`.

## Design passes

1. **Spacing:** regularized page, panel, card and navigation spacing; made the
   desktop navigation scroll when viewport height is constrained.
2. **Color:** moved shared surfaces and secondary text toward neutral tones;
   retained deep green for actions and selections. Shared text tokens and
   explicit status labels now meet a calculated 4.5:1 contrast threshold.
3. **Icons:** removed decorative overview stat icons and the evidence sparkle.
   Kept meaningful Lucide navigation, action and empty-state icons.
4. **Copy:** replaced the promotional overview eyebrow with
   “Inputs / Experiments / Evidence”; kept substantive claims and disclosures.
5. **Type:** raised small shared metadata to 12px, removed uppercase tracking
   from shared labels, and enabled tabular figures.
6. **Layout:** preserved section order and fluid content; compact horizontal
   scrolling navigation keeps every route available on small screens.
7. **Details:** removed the hero's decorative rings, kept its explanatory text,
   and retained the research-preview disclosure on small screens.
8. **Visuals:** no decorative images or invented data. Existing charts and
   lineage views remain the product's content; no chart logic changed.
9. **Motion:** changed shared buttons from a catch-all transition to a bounded
   color transition. Existing reduced-motion support is preserved.

## Validation

- Production build and TypeScript check: passed.
- Complete frontend unit suite: passed, including new text/status contrast and
  shared-component regression checks.
- Formatting of changed source and tests: passed.
- Diff whitespace check: passed.
- Backend and real-stack browser acceptance: not run for this presentation-only
  change. No PostgreSQL/Docker/provider integration claim is made.
- Visual browser inspection: blocked. The cloud browser could not reach the
  local Vite server; local Chromium could not create required sockets. An offline
  HTML render of the real shell/overview was produced with explicitly synthetic
  empty query data, but browser policy also refused that data URL. This is an
  inspection artifact, not successful screenshot or interaction validation.

Before merging, inspect desktop and phone widths, keyboard focus, navigation
scrolling, dense tables and actual research graph/result pages in a permitted
browser with a real backend.

## Method credit

Design audit and pass order adapted from **unslop by Damir Sokolovsky**
([@heydamir](https://x.com/heydamir)), licensed CC BY 4.0:
https://github.com/heydamir/unslop .
The skill was read as design guidance; its third-party scripts were not installed
or executed. This pass is a scoped polish, not a claim that every screen has
completed the skill's full visual checklist.
