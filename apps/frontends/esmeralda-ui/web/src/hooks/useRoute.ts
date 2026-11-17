// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import { useEffect, useState } from "react";

export type Route =
  | { view: "catalog"; target?: string }
  | { view: "finops" }
  | { view: "traces"; traceId?: string };

const SEGMENT_RE = /^[a-z0-9-]{1,63}$/;

/** Parses `#/catalog[/<target>]`, `#/finops`, `#/traces[/<trace id>]`; anything else is the catalog. */
export function parseRoute(hash: string): Route {
  const [view, arg] = hash.replace(/^#\/?/, "").split("/");
  const segment = arg && SEGMENT_RE.test(arg) ? arg : undefined;
  if (view === "finops") return { view: "finops" };
  if (view === "traces") return segment ? { view: "traces", traceId: segment } : { view: "traces" };
  return segment ? { view: "catalog", target: segment } : { view: "catalog" };
}

export function href(route: Route): string {
  if (route.view === "catalog") return route.target ? `#/catalog/${route.target}` : "#/catalog";
  if (route.view === "traces") return route.traceId ? `#/traces/${route.traceId}` : "#/traces";
  return "#/finops";
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
