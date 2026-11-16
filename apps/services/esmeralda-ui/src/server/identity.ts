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

import type { IncomingHttpHeaders } from "node:http";

/**
 * IAP Identity Verification & Persona Override for Policy Verification.
 *
 * In production behind Cloud Run IAP, reads `x-goog-iap-jwt-assertion` and
 * `x-goog-authenticated-user-email` (`accounts.google.com:<email>`).
 * When `ESMERALDA_UI_ALLOW_PERSONA_OVERRIDE=true` (in `dev`), allows switching
 * between synthetic corporate personas to test per-user and per-team policy enforcement.
 */

export interface UserPersona {
  id: string;
  email: string;
  display_name: string;
  role: string;
  team: string;
  cost_center: string;
  country: string;
  allowed_tools: string[];
  synthetic_assertion: string;
}

export const SYNTHETIC_PERSONAS: Record<string, UserPersona> = {
  "loan-officer": {
    id: "loan-officer",
    email: "loan-officer@megabank.example.com",
    display_name: "Sarah Jenkins (Senior Loan Officer)",
    role: "underwriting.loan_officer",
    team: "cx",
    cost_center: "cc-cx-br",
    country: "BR",
    allowed_tools: ["verify_applicant", "search_documents", "get_document", "send_email", "read_email"],
    synthetic_assertion: "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJsb2FuLW9mZmljZXJAbWVnYWJhbmsuZXhhbXBsZS5jb20iLCJyb2xlIjoidW5kZXJ3cml0aW5nLmxvYW5fb2ZmaWNlciIsInRlYW0iOiJjeCJ9.SYNTHETIC_SIG",
  },
  "contractor-marketing": {
    id: "contractor-marketing",
    email: "contractor-marketing@megabank.example.com",
    display_name: "Alex Rivera (Marketing Contractor)",
    role: "marketing.external_contractor",
    team: "unmapped-marketing",
    cost_center: "cc-mktg-ext",
    country: "US",
    allowed_tools: [],
    synthetic_assertion: "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJjb250cmFjdG9yLW1hcmtldGluZ0BtZWdhYmFuay5leGFtcGxlLmNvbSIsInJvbGUiOiJtYXJrZXRpbmcuZXh0ZXJuYWxfY29udHJhY3RvciIsInRlYW0iOiJ1bm1hcHBlZC1tYXJrZXRpbmcifQ.SYNTHETIC_SIG",
  },
  "platform-ops": {
    id: "platform-ops",
    email: "platform-ops@megabank.example.com",
    display_name: "Morgan Chen (Platform SRE)",
    role: "platform.admin",
    team: "platform",
    cost_center: "cc-platform",
    country: "BR",
    allowed_tools: ["verify_applicant", "search_documents", "get_document", "send_email", "read_email"],
    synthetic_assertion: "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJwbGF0Zm9ybS1vcHNAbWVnYWJhbmsuZXhhbXBsZS5jb20iLCJyb2xlIjoicGxhdGZvcm0uYWRtaW4iLCJ0ZWFtIjoicGxhdGZvcm0ifQ.SYNTHETIC_SIG",
  },
};

export interface VerifiedIdentity {
  email: string;
  subject: string;
  iap_jwt_present: boolean;
  persona_override_active: boolean;
  persona: UserPersona;
}

function decodeJwtPayload(jwt: string): Record<string, unknown> | null {
  const parts = jwt.split(".");
  if (parts.length !== 3 || !parts[1]) {
    return null;
  }
  try {
    const json = Buffer.from(parts[1], "base64url").toString("utf-8");
    const parsed = JSON.parse(json);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function isPersonaOverrideAllowed(env: NodeJS.ProcessEnv = process.env): boolean {
  const raw = (env.ESMERALDA_UI_ALLOW_PERSONA_OVERRIDE || "").trim().toLowerCase();
  if (raw === "true" || raw === "1" || raw === "yes") {
    return true;
  }
  if (raw === "false" || raw === "0" || raw === "no") {
    return false;
  }
  return (env.ENVIRONMENT || "dev").trim().toLowerCase() === "dev";
}

export function resolveCallerIdentity(
  headers: IncomingHttpHeaders,
  requestedPersonaId?: string,
  env: NodeJS.ProcessEnv = process.env
): VerifiedIdentity {
  const allowOverride = isPersonaOverrideAllowed(env);
  const overrideHeader = typeof headers["x-esmeralda-persona"] === "string" ? headers["x-esmeralda-persona"].trim() : "";
  const chosenPersonaKey = requestedPersonaId || overrideHeader;

  if (allowOverride && chosenPersonaKey && SYNTHETIC_PERSONAS[chosenPersonaKey]) {
    const persona = SYNTHETIC_PERSONAS[chosenPersonaKey];
    return {
      email: persona.email,
      subject: `accounts.google.com:${persona.email}`,
      iap_jwt_present: Boolean(headers["x-goog-iap-jwt-assertion"]),
      persona_override_active: true,
      persona,
    };
  }

  const iapJwt = typeof headers["x-goog-iap-jwt-assertion"] === "string" ? headers["x-goog-iap-jwt-assertion"].trim() : "";
  const iapEmailHeader =
    typeof headers["x-goog-authenticated-user-email"] === "string"
      ? headers["x-goog-authenticated-user-email"].trim()
      : "";

  let email = "";
  let sub = "";

  if (iapJwt) {
    const claims = decodeJwtPayload(iapJwt);
    if (claims) {
      if (typeof claims.email === "string") email = claims.email.toLowerCase();
      if (typeof claims.sub === "string") sub = claims.sub;
      // Support Identity Platform (GCIP) IAP JWT nested claims (`gcip.firebase.identities.email`)
      if (!email && claims.gcip && typeof claims.gcip === "object") {
        const gcip = claims.gcip as Record<string, unknown>;
        if (typeof gcip.email === "string") {
          email = gcip.email.toLowerCase();
        } else if (gcip.firebase && typeof gcip.firebase === "object") {
          const identities = (gcip.firebase as Record<string, unknown>).identities as Record<string, unknown> | undefined;
          const emails = identities?.email;
          if (Array.isArray(emails) && typeof emails[0] === "string") {
            email = emails[0].toLowerCase();
          }
        }
      }
    }
  }

  if (!email && iapEmailHeader) {
    email = iapEmailHeader.replace(/^accounts\.google\.com:/i, "").toLowerCase();
  }

  const defaultPersona = SYNTHETIC_PERSONAS["loan-officer"];
  if (!email) {
    return {
      email: defaultPersona.email,
      subject: `accounts.google.com:${defaultPersona.email}`,
      iap_jwt_present: false,
      persona_override_active: false,
      persona: defaultPersona,
    };
  }

  const matched =
    Object.values(SYNTHETIC_PERSONAS).find((p) => p.email.toLowerCase() === email) || {
      ...defaultPersona,
      id: email.split("@")[0] || "authenticated-user",
      email,
      display_name: email,
    };

  return {
    email,
    subject: sub || `accounts.google.com:${email}`,
    iap_jwt_present: Boolean(iapJwt),
    persona_override_active: false,
    persona: matched,
  };
}
