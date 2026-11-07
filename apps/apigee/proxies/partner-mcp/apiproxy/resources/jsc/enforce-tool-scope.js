/**
 * Copyright 2026 Google LLC
 * Licensed under the Apache License, Version 2.0.
 * Adapted from github.com/ricardosmh/adk-apigee-multiagent-demo (Apache-2.0).
 *
 * Server-side MCP tool scope enforcement:
 * Inspects JSON-RPC 2.0 `tools/call` requests and verifies `params.name`
 * against the API product's `allowed_mcp_tools` attribute (comma-separated
 * tool names or `*` wildcard). Discovery methods such as `initialize` and
 * `tools/list` are permitted so MCP clients can handshake cleanly.
 */
(function () {
  context.setVariable('mcp.tool.forbidden', false);

  var pathSuffix = String(context.getVariable('proxy.pathsuffix') || '');
  var match = pathSuffix.match(/^\/([^\/]+)\/mcp/);
  var serviceHost = match && match[1] ? match[1] : 'corporate-email';
  context.setVariable('mcp.service.host', serviceHost);
  context.setVariable('target.audience', 'https://' + serviceHost + '.esmeralda.internal');

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
    var allowedStr = String(allowedRaw || '').trim();
    if (!allowedStr) {
      context.setVariable('mcp.tool.forbidden', true);
      return;
    }

    var parts = allowedStr.split(',');
    var allowed = false;
    for (var i = 0; i < parts.length; i++) {
      var candidate = parts[i].replace(/^\s+|\s+$/g, '');
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
