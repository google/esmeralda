local http = require "resty.http"
local kong = kong

local GCPServiceAccountHandler = {}

GCPServiceAccountHandler.PRIORITY = 800
GCPServiceAccountHandler.VERSION = "0.1.0"

function GCPServiceAccountHandler:access(config)
  local audience = config.audience
  if not audience or audience == "" then
    kong.log.warn("gcp-service-account: audience is not configured")
    return
  end

  local httpc = http.new()
  local res, err = httpc:request_uri("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity", {
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

  if not res then
    kong.log.err("gcp-service-account: failed to fetch OIDC token from metadata server: ", err)
    return
  end

  if res.status ~= 200 then
    kong.log.err("gcp-service-account: metadata server returned status ", res.status, " body: ", res.body)
    return
  end

  local token = res.body
  if not token or token == "" then
    kong.log.err("gcp-service-account: metadata server returned empty token")
    return
  end

  kong.service.request.set_header("Authorization", "Bearer " .. token)
end

return GCPServiceAccountHandler
