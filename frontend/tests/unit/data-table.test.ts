import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { DataTable, type ColumnDef } from "@/components/data-table";

it("renders all supplied server-page rows and bound cell values", () => {
  const data = Array.from({ length: 12 }, (_, index) => ({
    value: `Row ${index + 1}`,
  }));
  const columns: ColumnDef<(typeof data)[number]>[] = [
    {
      header: "Server result",
      accessorKey: "value",
      cell: ({ getValue }) => `Value: ${getValue()}`,
    },
  ];

  const html = renderToStaticMarkup(
    createElement(DataTable<(typeof data)[number]>, {
      columns,
      data,
      label: "Current server page",
    }),
  );

  expect(html).toContain(
    '<caption class="sr-only">Current server page</caption>',
  );
  expect(html).toContain('<th scope="col">Server result</th>');
  expect(html.match(/<td>/g)).toHaveLength(12);
  expect(html).toContain("<td>Value: Row 12</td>");
});

it("supports array-backed result columns and still renders its empty state", () => {
  const columns: ColumnDef<[string, number]>[] = [
    { id: "symbol", header: "Symbol", accessorFn: (row) => row[0] },
    { id: "quantity", header: "Quantity", accessorFn: (row) => row[1] },
  ];
  const table = (data: [string, number][]) =>
    renderToStaticMarkup(
      createElement(DataTable<[string, number]>, {
        columns,
        data,
        label: "Returned trades",
      }),
    );

  expect(table([["Example", 0]])).toContain("<td>Example</td><td>0</td>");
  expect(table([])).toContain("No records in this page.");
});
