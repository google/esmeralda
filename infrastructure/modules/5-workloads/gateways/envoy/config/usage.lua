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

-- No token, body or arbitrary request header is copied into the access-log metadata.
local namespace = "esmeralda.usage"

-- Decode the flat JSON payload of an ID token that Cloud Run IAM has validated.
local escapes = {
  ['"'] = '"', ['\\'] = '\\', ['/'] = '/',
  b = '\b', f = '\f', n = '\n', r = '\r', t = '\t',
}

local function utf8_char(code)
  local floor, char = math.floor, string.char
  if code < 0x80 then return char(code) end
  if code < 0x800 then
    return char(0xc0 + floor(code / 64), 0x80 + code % 64)
  end
  if code < 0x10000 then
    return char(0xe0 + floor(code / 4096), 0x80 + floor(code / 64) % 64, 0x80 + code % 64)
  end
  return char(0xf0 + floor(code / 262144), 0x80 + floor(code / 4096) % 64,
    0x80 + floor(code / 64) % 64, 0x80 + code % 64)
end

local function unicode_escape(text, pos)
  local digits = text:sub(pos, pos + 3)
  if not digits:match("^%x%x%x%x$") then return nil end
  local code = tonumber(digits, 16)
  pos = pos + 4
  if code >= 0xd800 and code <= 0xdbff then
    local low = text:match("^\\u(%x%x%x%x)", pos)
    low = low and tonumber(low, 16)
    if not low or low < 0xdc00 or low > 0xdfff then return nil end
    code = 0x10000 + (code - 0xd800) * 0x400 + low - 0xdc00
    pos = pos + 6
  elseif code >= 0xdc00 and code <= 0xdfff then
    return nil
  end
  return utf8_char(code), pos
end

local function json_string(text, pos)
  if text:sub(pos, pos) ~= '"' then return nil end
  local parts = {}
  pos = pos + 1
  while true do
    local stop = text:find('[%z\1-\31"\\]', pos)
    if not stop then return nil end
    parts[#parts + 1] = text:sub(pos, stop - 1)
    local next_char = text:sub(stop, stop)
    if next_char == '"' then return table.concat(parts), stop + 1 end
    if next_char ~= '\\' then return nil end
    local escape = text:sub(stop + 1, stop + 1)
    local value = escapes[escape]
    pos = stop + 2
    if escape == "u" then value, pos = unicode_escape(text, pos) end
    if value == nil then return nil end
    parts[#parts + 1] = value
  end
end

local function json_scalar(text, pos)
  if text:sub(pos, pos) == '"' then return json_string(text, pos) end
  local token, next_pos = text:match("^([^ \t\r\n,}]+)()", pos)
  if token == "true" then return true, next_pos end
  if token == "false" then return false, next_pos end
  if not token then return nil end
  local integer, tail = token:match("^(-?%d+)(.*)$")
  if not integer or integer:match("^-?0%d") then return nil end
  if tail:sub(1, 1) == "." then tail = tail:match("^%.%d+(.*)$") end
  if not tail or (tail ~= "" and not tail:match("^[eE][+-]?%d+$")) then return nil end
  return tonumber(token), next_pos
end

local function skip_space(text, pos)
  return text:match("^[ \t\r\n]*()", pos)
end

local function decode_claims(text)
  local pos = skip_space(text, 1)
  if text:sub(pos, pos) ~= "{" then return nil end
  local claims = {}
  pos = skip_space(text, pos + 1)
  if text:sub(pos, pos) == "}" then
    if skip_space(text, pos + 1) > #text then return claims end
    return nil
  end
  while true do
    local key, next_pos = json_string(text, pos)
    if key == nil or claims[key] ~= nil then return nil end
    pos = skip_space(text, next_pos)
    if text:sub(pos, pos) ~= ":" then return nil end
    local value
    value, next_pos = json_scalar(text, skip_space(text, pos + 1))
    if value == nil then return nil end
    claims[key] = value
    pos = skip_space(text, next_pos)
    local separator = text:sub(pos, pos)
    if separator == "}" and skip_space(text, pos + 1) > #text then return claims end
    if separator ~= "," then return nil end
    pos = skip_space(text, pos + 1)
  end
end

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
  local claims = decode_claims(decoded)
  if claims == nil then return nil end
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
