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

/**
 * Lightweight Google Cloud metadata server token client for Cloud Run / GCE.
 * Zero external npm dependencies required; degrades cleanly to null in offline/test environments.
 */

const METADATA_BASE = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default";

export async function fetchAccessToken(env: NodeJS.ProcessEnv = process.env): Promise<string | null> {
  if (env.ESMERALDA_OFFLINE_MODE === "true" || env.NODE_ENV === "test") {
    return null;
  }
  try {
    const res = await fetch(`${METADATA_BASE}/token`, {
      headers: { "Metadata-Flavor": "Google" },
      signal: AbortSignal.timeout(1500),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { access_token?: string };
    return data.access_token || null;
  } catch {
    return null;
  }
}

export async function fetchIdToken(audience: string, env: NodeJS.ProcessEnv = process.env): Promise<string | null> {
  if (env.ESMERALDA_OFFLINE_MODE === "true" || env.NODE_ENV === "test") {
    return null;
  }
  try {
    const url = `${METADATA_BASE}/identity?audience=${encodeURIComponent(audience)}&format=full`;
    const res = await fetch(url, {
      headers: { "Metadata-Flavor": "Google" },
      signal: AbortSignal.timeout(1500),
    });
    if (!res.ok) return null;
    const text = (await res.text()).trim();
    return text || null;
  } catch {
    return null;
  }
}
