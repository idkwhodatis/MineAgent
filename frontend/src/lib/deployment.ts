/** Operator-supplied deployment prefix, never inferred from proxy headers or URL. */
export function normalizeSubpath(value: string): string {
  if (value === "/") return "";
  if (
    value.length > 200 ||
    /[^/A-Za-z0-9._-]/.test(value) ||
    !/^(\/[A-Za-z0-9._-]+)*$/.test(value) ||
    value.split("/").some((part) => part === "." || part === "..")
  )
    throw new Error("Invalid deployment SUBPATH");
  return value;
}
export const APP_SUBPATH = normalizeSubpath(
  typeof document === "undefined" ||
    typeof document.querySelector !== "function"
    ? ""
    : (document.querySelector<HTMLMetaElement>('meta[name="mineagent-subpath"]')
        ?.content ?? ""),
);
export function apiBaseUrl(): string {
  return (
    (typeof window === "undefined" ? "" : (window.location?.origin ?? "")) +
    APP_SUBPATH
  );
}
export function deploymentUrl(path: string): string {
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\"))
    throw new Error("Expected application-relative path");
  return APP_SUBPATH + path;
}

export function deploymentHref(
  path: string | null | undefined,
): string | undefined {
  return path ? deploymentUrl(path) : undefined;
}
export function fragmentHref(fragment: string): string {
  return (
    (typeof window === "undefined"
      ? APP_SUBPATH + "/"
      : (window.location?.pathname ?? APP_SUBPATH + "/") +
        (window.location?.search ?? "")) + fragment
  );
}
