import { queryOptions } from "@tanstack/react-query";
import { factorRequest } from "./factor-api";
import type {
  SteerView,
  SteerPage,
  SteerHistory,
  SteerCapability,
  SteerVersion,
} from "@/client/types.gen";
export type { SteerView, SteerPage, SteerHistory, SteerVersion };
export type SteerKind = SteerVersion["kind"];
export const steerCapabilityOptions = queryOptions({
  queryKey: ["rd-steers", "capabilities"],
  retry: false,
  queryFn: ({ signal }) =>
    factorRequest<SteerCapability>("/rd-steers/capabilities", { signal }),
});
export const steerListOptions = queryOptions({
  queryKey: ["rd-steers", "list"],
  retry: false,
  queryFn: ({ signal }) =>
    factorRequest<SteerPage>("/rd-steers?limit=100", { signal }),
});
export const steerHistoryOptions = (id: string) =>
  queryOptions({
    queryKey: ["rd-steers", id, "versions"],
    retry: false,
    queryFn: ({ signal }) =>
      factorRequest<SteerHistory>(
        `/rd-steers/${encodeURIComponent(id)}/versions`,
        { signal },
      ),
  });
export function appendSteerSelection(selected: string[], version: string) {
  if (selected.includes(version)) return selected;
  if (selected.length >= 8)
    throw new Error("Choose at most eight steer versions.");
  return [...selected, version];
}
export function moveSteerSelection(
  selected: string[],
  index: number,
  delta: -1 | 1,
) {
  const target = index + delta;
  if (
    index < 0 ||
    target < 0 ||
    index >= selected.length ||
    target >= selected.length
  )
    return selected;
  const next = [...selected];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}
