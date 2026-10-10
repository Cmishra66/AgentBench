import { useEffect, useState } from "react";

export type Route = { page: string; params: URLSearchParams };

function parse(): Route {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [page, query] = raw.split("?");
  return { page: page || "overview", params: new URLSearchParams(query || "") };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(parse);
  useEffect(() => {
    const onChange = () => setRoute(parse());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function href(page: string, params: Record<string, string | number | undefined | null> = {}): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return `#/${page}${s ? `?${s}` : ""}`;
}

export function navigate(page: string, params: Record<string, string | number | undefined | null> = {}): void {
  window.location.hash = href(page, params);
}

/** Replace query params on the current page without adding a history entry. */
export function replaceParams(page: string, params: Record<string, string | number | undefined | null>): void {
  const url = `${window.location.pathname}${window.location.search}${href(page, params)}`;
  window.history.replaceState(null, "", url);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}
