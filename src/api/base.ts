/**
 * mgllm: the path under which this dashboard is served (Vite's `base`, from VITE_BASE at build time; "/" by default),
 * so it also works behind a reverse proxy on a sub-path such as /sparkdash/. withBase("/api/x") is "/api/x" at "/" and
 * "/sparkdash/api/x" under /sparkdash/.
 */
export const withBase = (path: string): string => import.meta.env.BASE_URL.replace(/\/$/, "") + path;

/** The app's own path for an address under the base: "/sparkdash/spark/x" is "/spark/x", "/sparkdash" is "/". */
export function stripBase(pathname: string): string {
  const base = import.meta.env.BASE_URL.replace(/\/$/, "");
  if (!base) return pathname;
  if (pathname === base) return "/";
  return pathname.startsWith(`${base}/`) ? pathname.slice(base.length) : pathname;
}
