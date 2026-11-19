-- Copyright 2026 Google LLC
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
--     https://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

-- json is the MIT-licensed rxi/json.lua 0.1.2 decoder, embedded by the config module.
-- No token, body or arbitrary request header is copied into the access-log metadata.
local namespace = "esmeralda.usage"

local function principal(handle)
  if not token_validated_by_cloud_run_iam then
    return nil
  end
  local headers = handle:headers()
  -- Cloud Run validates the first header present, even if it is empty or malformed.
  -- Never fall back to Authorization when X-Serverless-Authorization was supplied.
  local value = headers:get("x-serverless-authorization")
  if value == nil then value = headers:get("authorization") end
  if value == nil or #value > 8192 then return nil end
  value = value:match("^%s*(.-)%s*$")
  if value:sub(1, 7):lower() == "bearer " then value = value:sub(8) end
  local payload = value:match("^[^.]+%.([^.]+)%.[^.]*$")
  if payload == nil then return nil end
  payload = payload:gsub("-", "+"):gsub("_", "/")
  local decoded = handle:base64Decode(payload .. string.rep("=", (4 - #payload % 4) % 4))
  if decoded == nil then return nil end
  local ok, claims = pcall(json.decode, decoded)
  if not ok or type(claims) ~= "table" then return nil end
  local result = claims.sub
  if type(claims.email) == "string" and claims.email ~= "" and claims.email_verified ~= false then
    result = claims.email
  end
  if type(result) ~= "string" then return nil end
  result = result:match("^%s*(.-)%s*$")
  if #result == 0 or #result > 512 then return nil end
  if result:sub(1, 9) == "spiffe://" then result = "principal://" .. result:sub(10) end
  if result:find("@", 1, true) and not result:find("://", 1, true) then result = result:lower() end
  if #result > 512 then return nil end
  return result
end

function envoy_on_request(handle)
  local metadata = handle:streamInfo():dynamicMetadata()
  local identity = principal(handle)
  local caller = { verified = identity ~= nil }
  if identity ~= nil then caller.principal = identity end
  metadata:set(namespace, "caller", caller)

  -- Save the caller-facing path before Envoy rewrites it; query strings may carry credentials.
  local path = (handle:headers():get(":path") or ""):match("^[^?]*")
  if path ~= "" then metadata:set(namespace, "route", path:sub(1, 512)) end
  local traceparent = handle:headers():get("traceparent") or ""
  local version, trace, parent = traceparent:match("^(%x%x)%-(%x+)%-(%x+)%-(%x%x)$")
  if version and version ~= "ff" and #trace == 32 and #parent == 16
      and traceparent == traceparent:lower() and trace ~= string.rep("0", 32)
      and parent ~= string.rep("0", 16) then
    metadata:set(namespace, "trace_id", trace)
  end
end
