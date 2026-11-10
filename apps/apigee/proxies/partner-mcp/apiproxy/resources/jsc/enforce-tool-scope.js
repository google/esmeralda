/**
 * Copyright 2026 Google LLC
 * Licensed under the Apache License, Version 2.0.
 * Adapted from github.com/ricardosmh/adk-apigee-multiagent-demo (Apache-2.0).
 *
 * Partner MCP routing and server-side tool scope enforcement:
 * 1. Resolves the MCP service from the path (/<service>/mcp) and checks it against the
 *    `allowed_mcp_services` entry of kvm-partner-mcp (only services exposed to partners are
 *    reachable; any other host of the entry point stays internal). Sets the southbound Host
 *    (<service>.<apex_domain>) and the Google ID token audience (https://<service>.<apex_domain>).
 * 2. Inspects JSON-RPC 2.0 `tools/call` requests and verifies `params.name`
 *    against the API product's `allowed_mcp_tools` attribute (comma-separated
 *    tool names, `<service>:<tool>`, or `*` wildcard). Discovery methods such as `initialize` and
 *    `tools/list` are permitted so MCP clients can handshake cleanly.
 */
(function () {
  context.setVariable('mcp.tool.forbidden', false);
  context.setVariable('mcp.service.allowed', false);

  function csv(raw) {
    var out = [];
    var parts = String(raw || '').split(',');
    for (var i = 0; i < parts.length; i++) {
      var item = parts[i].replace(/^\s+|\s+$/g, '');
      if (item) {
        out.push(item);
      }
    }
    return out;
  }

  var pathSuffix = String(context.getVariable('proxy.pathsuffix') || '');
  var match = pathSuffix.match(/^\/([a-z0-9][a-z0-9-]*)\/mcp\/?$/);
  var serviceHost = match && match[1] ? match[1] : '';
  var domain = String(context.getVariable('mcp.apex_domain') || 'esmeralda.internal').replace(/^\s+|\s+$/g, '');
  var allowedServices = csv(context.getVariable('mcp.allowed_services'));

  context.setVariable('mcp.service.host', serviceHost);
  if (!serviceHost || allowedServices.indexOf(serviceHost) === -1) {
    return;
  }
  context.setVariable('mcp.service.allowed', true);
  context.setVariable('mcp.target.host', serviceHost + '.' + domain);
  // Not target.*: Apigee resets target.* variables when the target flow starts.
  context.setVariable('mcp.target.audience', 'https://' + serviceHost + '.' + domain);

  var rawBody = context.getVariable('request.content');
  if (!rawBody) {
    return;
  }

  try {
    var rpc = JSON.parse(String(rawBody));
    var method = rpc && rpc.method ? String(rpc.method) : '';
    context.setVariable('mcp.rpc.method', method);

    if (method !== 'tools/call') {
      return;
    }

    var toolName = rpc.params && rpc.params.name ? String(rpc.params.name).trim() : '';
    context.setVariable('mcp.tool.name', toolName);
    if (!toolName) {
      context.setVariable('mcp.tool.forbidden', true);
      return;
    }

    var allowedRaw = context.getVariable(
      'verifyapikey.VAK-Verify-Partner-Key.apiproduct.allowed_mcp_tools'
    );
    if (allowedRaw == null) {
      allowedRaw = context.getVariable('apiproduct.allowed_mcp_tools');
    }
    var parts = csv(allowedRaw);
    if (!parts.length) {
      context.setVariable('mcp.tool.forbidden', true);
      return;
    }

    var allowed = false;
    for (var i = 0; i < parts.length; i++) {
      var candidate = parts[i];
      if (
        candidate === '*' ||
        candidate === toolName ||
        candidate === serviceHost + ':' + toolName
      ) {
        allowed = true;
        break;
      }
    }

    if (!allowed) {
      context.setVariable('mcp.tool.forbidden', true);
    }
  } catch (_err) {
    context.setVariable('mcp.tool.forbidden', true);
  }
})();
