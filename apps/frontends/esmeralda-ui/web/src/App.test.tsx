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

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { api, ApiError } from "./api/client";
import { parseRoute } from "./hooks/useRoute";
import { CATALOG, ME, stubFetch, TRACE, TRACE_ID, TRACES, USAGE } from "./test/setup";
import { CatalogView } from "./views/CatalogView";
import { FinOpsView } from "./views/FinOpsView";
import { TracesView } from "./views/TracesView";

describe("App shell", () => {
  it("shows the verified identity and no demo badge for live data", async () => {
    stubFetch({ "/api/me": { body: ME }, "/api/catalog": { body: CATALOG } });
    render(<App />);
    expect(await screen.findByText("julian.sterling@example.com")).toBeTruthy();
    expect(screen.getByText("IAP verified")).toBeTruthy();
    expect(screen.queryByText("Demo data")).toBeNull();
  });

  it("badges demo mode and the local identity", async () => {
    stubFetch({ "/api/me": { body: { ...ME, demo: true, auth: "local-dev" } }, "/api/catalog": { body: CATALOG } });
    render(<App />);
    expect(await screen.findByText("Demo data")).toBeTruthy();
    expect(screen.getByText("Local dev identity (not verified)")).toBeTruthy();
  });

  it("shows an error state, not a default persona, when the identity is rejected", async () => {
    stubFetch({ "/api/me": { status: 401, body: { error: "unauthenticated" } } });
    render(<App />);
    expect(await screen.findByText("Not signed in")).toBeTruthy();
    expect(screen.queryByText(/example\.com/)).toBeNull();
  });
});

describe("Service Catalog", () => {
  it("lists targets and opens the callers of one", async () => {
    stubFetch({ "/api/catalog": { body: CATALOG } });
    render(<CatalogView selected="income-verification" />);
    expect(await screen.findByText("Targets (2)")).toBeTruthy();
    expect(screen.getByText("Who may call it, and who pays")).toBeTruthy();
    expect(screen.getAllByText("ai-coe-mortgage-specialist").length).toBeGreaterThan(0);
    expect(screen.getByText("enforced")).toBeTruthy();
  });

  it("renders catalog text as text, never as HTML", async () => {
    stubFetch({ "/api/catalog": { body: CATALOG } });
    render(<CatalogView selected="cx-mortgage-orchestrator" />);
    expect(await screen.findByText('<img src="x" data-testid="injected">')).toBeTruthy();
    expect(screen.queryByTestId("injected")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
  });

  it("filters by text", async () => {
    stubFetch({ "/api/catalog": { body: CATALOG } });
    render(<CatalogView />);
    fireEvent.change(await screen.findByLabelText("Filter targets"), { target: { value: "income" } });
    expect(screen.getByText("Targets (1)")).toBeTruthy();
  });

  it("shows the 503 error state instead of a synthetic catalog", async () => {
    stubFetch({ "/api/catalog": { status: 503, body: { error: "catalog unavailable" } } });
    render(<CatalogView />);
    expect(await screen.findByText("Data unavailable")).toBeTruthy();
    expect(screen.queryByText(/Targets/)).toBeNull();
  });
});

describe("FinOps & Audit", () => {
  it("renders chargeback rows with a live badge", async () => {
    stubFetch({
      "/api/usage": { body: USAGE },
      "/api/audit": { body: { source: "bigquery", days: 7, entries: [] } },
    });
    render(<FinOpsView />);
    expect(await screen.findByText("By billed team")).toBeTruthy();
    expect(screen.getAllByText("Live · BigQuery").length).toBe(2);
    expect(screen.getByText("No audited changes in the last 7 days.")).toBeTruthy();
  });

  it("badges demo data on every card", async () => {
    stubFetch({
      "/api/usage": { body: { ...USAGE, source: "demo" } },
      "/api/audit": { body: { source: "demo", days: 7, entries: [] } },
    });
    render(<FinOpsView />);
    expect((await screen.findAllByText("Demo data")).length).toBe(2);
  });

  it("shows an error state when no data source is configured", async () => {
    stubFetch({
      "/api/usage": { status: 503, body: { error: "data source unavailable" } },
      "/api/audit": { status: 503, body: { error: "data source unavailable" } },
    });
    render(<FinOpsView />);
    expect((await screen.findAllByText("Data unavailable")).length).toBe(2);
  });

  it("sends the month and team as query parameters", async () => {
    const { calls } = stubFetch({
      "/api/usage": { body: USAGE },
      "/api/audit": { body: { source: "bigquery", days: 7, entries: [] } },
    });
    render(<FinOpsView />);
    await screen.findByText("By billed team");
    fireEvent.change(screen.getByLabelText("Billed team"), { target: { value: "cx" } });
    await screen.findByText("By billed team");
    expect(calls.some((c) => c.startsWith("/api/usage?") && c.includes("team=cx"))).toBe(true);
  });
});

describe("Traces", () => {
  it("lists recent traces and draws the waterfall of one", async () => {
    stubFetch({ "/api/traces": { body: TRACES }, [`/api/traces/${TRACE_ID}`]: { body: TRACE } });
    render(<TracesView traceId={TRACE_ID} />);
    const waterfall = await screen.findByLabelText("Trace waterfall");
    expect(within(waterfall).getAllByRole("listitem").length).toBe(2);
    expect(screen.getAllByText("Demo data").length).toBeGreaterThan(0);
  });

  it("rejects an invalid trace id without calling the API", async () => {
    const { calls } = stubFetch({ "/api/traces": { body: TRACES } });
    render(<TracesView />);
    await screen.findByText(TRACE_ID);
    fireEvent.change(screen.getByLabelText("Trace id"), { target: { value: 'x" OR logName:"y' } });
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(screen.getByRole("alert").textContent).toContain("32 hexadecimal");
    expect(calls.every((c) => !c.includes("logName"))).toBe(true);
  });
});

describe("api client", () => {
  it("never requests an invalid trace id", async () => {
    const { fetchMock } = stubFetch({});
    await expect(api.trace("../../etc")).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("surfaces the generic error message", async () => {
    stubFetch({ "/api/audit": { status: 503, body: { error: "data source unavailable" } } });
    await expect(api.audit(7)).rejects.toMatchObject({ status: 503, message: "data source unavailable" });
  });

  it("parses routes defensively", () => {
    expect(parseRoute("#/traces/abc")).toEqual({ view: "traces", traceId: "abc" });
    expect(parseRoute("#/catalog/<script>")).toEqual({ view: "catalog" });
    expect(parseRoute("#/unknown")).toEqual({ view: "catalog" });
  });
});

describe("source hygiene", () => {
  const sources = import.meta.glob(["./**/*.{ts,tsx}", "!./**/*.test.{ts,tsx}", "!./test/**"], {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>;

  it("has sources to scan", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(5);
  });

  it.each(["dangerouslySetInnerHTML", "innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function("])(
    "never uses %s",
    (needle) => {
      for (const [file, text] of Object.entries(sources)) {
        expect(text.includes(needle), `${file} uses ${needle}`).toBe(false);
      }
    },
  );

  it("never calls an external origin", () => {
    for (const [file, text] of Object.entries(sources)) {
      if (file.endsWith("schema.ts")) continue;
      expect(/https?:\/\//.test(text.replace(/\/\/ .*$/gm, "")), `${file} has an absolute URL`).toBe(false);
    }
  });
});
