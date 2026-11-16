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

import { type DependencyList, useCallback, useEffect, useState } from "react";

export type Async<T> =
  | { state: "loading" }
  | { state: "error"; error: Error }
  | { state: "ok"; data: T };

/** Runs `load` when `deps` change (aborting the previous run) and exposes its state. */
export function useAsync<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: DependencyList,
): Async<T> & { reload: () => void } {
  const [result, setResult] = useState<Async<T>>({ state: "loading" });
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setResult({ state: "loading" });
    load(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setResult({ state: "ok", data });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setResult({ state: "error", error: error instanceof Error ? error : new Error(String(error)) });
        }
      },
    );
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { ...result, reload };
}
