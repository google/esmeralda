local http = require "resty.http"
local cjson = require "cjson.safe"
local kong = kong

local GCPServiceAccountHandler = {}

GCPServiceAccountHandler.PRIORITY = 800
GCPServiceAccountHandler.VERSION = "0.2.0"

function GCPServiceAccountHandler:access(config)
  local audience = config.audience
  if not audience or audience == "" then
    kong.log.warn("gcp-service-account: audience is not configured")
    return
  end

  local httpc = http.new()
  local is_google_api = string.find(audience, "googleapis.com") ~= nil
  local res, err

  if is_google_api then
    -- Fetch OAuth2 Access Token for Google Cloud APIs (Vertex AI / Reasoning Engine)
    res, err = httpc:request_uri("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token", {
      method = "GET",
      headers = {
        ["Metadata-Flavor"] = "Google",
      },
      keepalive_timeout = 60000,
      keepalive_pool = 10
    })
  else
    -- Fetch OIDC Identity Token for Cloud Run services
    res, err = httpc:request_uri("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity", {
      method = "GET",
      query = {
        audience = audience
      },
      headers = {
        ["Metadata-Flavor"] = "Google",
      },
      keepalive_timeout = 60000,
      keepalive_pool = 10
    })
  end

  if not res then
    kong.log.err("gcp-service-account: failed to fetch token from metadata server: ", err)
    return
  end

  if res.status ~= 200 then
    kong.log.err("gcp-service-account: metadata server returned status ", res.status, " body: ", res.body)
    return
  end

  local token
  if is_google_api then
    local data = cjson.decode(res.body)
    if data and data.access_token then
      token = data.access_token
    end
  else
    token = res.body
  end

  if not token or token == "" then
    kong.log.err("gcp-service-account: metadata server returned empty token")
    return
  end

  kong.service.request.set_header("Authorization", "Bearer " .. token)
end

return GCPServiceAccountHandler
