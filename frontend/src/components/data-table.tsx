import {
  flexRender,
  tableFeatures,
  useTable,
  type ColumnDef as TanStackColumnDef,
  type RowData,
} from "@tanstack/react-table";

// Use only the core row model. Server endpoints own paging and filtering,
// including for the separate virtualized presentation.
export const serverPageFeatures = tableFeatures({});
export type ColumnDef<T extends RowData> = TanStackColumnDef<
  typeof serverPageFeatures,
  T
>;

// Server endpoints own filtering and paging. At most one bounded page enters the
// table; don't download an entire history to paginate it in the browser.
export function DataTable<T extends RowData>({
  data,
  columns,
  label,
  empty = "No records in this page.",
}: {
  data: T[];
  columns: ColumnDef<T>[];
  label: string;
  empty?: string;
}) {
  const table = useTable({
    data,
    columns,
    features: serverPageFeatures,
  });
  return (
    <div className="table-scroll" tabIndex={0} role="region" aria-label={label}>
      <table>
        <caption className="sr-only">{label}</caption>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => (
                <th key={header.id} scope="col">
                  {header.isPlaceholder
                    ? null
                    : flexRender(
                        header.column.columnDef.header,
                        header.getContext(),
                      )}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getAllCells().map((cell) => (
                <td key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!data.length && <p className="empty">{empty}</p>}
    </div>
  );
}
