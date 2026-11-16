/**
 * Copyright 2026 Google LLC
 * Licensed under the Apache License, Version 2.0.
 *
 * Computes the parts of the `esmeralda.usage` record (contracts/usage-event.schema.json)
 * that a MessageLogging template cannot: an RFC 3339 timestamp, latency_ms, the outcome,
 * the W3C trace id, and optional JSON fragments (a fragment is either empty or ends with
 * a comma, so ML-interaction stays valid JSON whichever fields are present).
 *
 * Run it after authentication, again at the end of the exchange (response PostFlow, final
 * SSE event) and in the DefaultFaultRule: each run overwrites the previous values, so the
 * record always describes the latest state. It never reads request or response bodies.
 *
 * Policy <Properties> (all optional):
 *   principal_ref     flow variable(s) holding the authenticated caller principal, comma-separated:
 *                     the first non-empty value other than "anonymous" wins
 *   phase             "fault" in the DefaultFaultRule: the outcome comes from the fault
 *   workload_ref      flow variable holding the target workload name
 *   workload_from_path "true": the first proxy.pathsuffix segment is the target workload
 *   workload_runtime  agent_engine | cloud_run | gke | external
 *   tool_ref          flow variable holding the MCP tool name (tools/call only)
 *   tool_server_ref   flow variable holding the MCP server name
 *   gen_ai            always | tokens | none (default: tokens = only when token counts exist)
 *   gen_ai_system     gen_ai.system value (e.g. gcp.vertex_ai)
 *   operation_name    gen_ai.operation.name value (e.g. generate_content)
 *   request_model_ref flow variable holding the requested model
 */
(function () {
  var props = typeof properties === 'object' && properties ? properties : {};

  function prop(name) {
    var value = props[name];
    return value == null ? '' : String(value).trim();
  }

  function str(name) {
    if (!name) {
      return '';
    }
    var value = context.getVariable(name);
    return value == null ? '' : String(value).trim();
  }

  function count(name) {
    var raw = context.getVariable(name);
    if (raw == null || raw === '') {
      return null;
    }
    var value = Math.round(Number(raw));
    return isFinite(value) && value >= 0 ? value : null;
  }

  function clip(value, max) {
    return value.length > max ? value.substring(0, max) : value;
  }

  function q(value) {
    return JSON.stringify(String(value));
  }

  var now = Number(context.getVariable('system.timestamp')) || new Date().getTime();
  var start = Number(context.getVariable('client.received.start.timestamp')) || now;
  context.setVariable('usage.timestamp', new Date(now).toISOString());
  context.setVariable('usage.latency_ms', String(Math.max(0, now - start)));

  // W3C traceparent: <version>-<trace-id>-<parent-id>-<flags>. The parent id is the caller's
  // span, not this gateway hop, so only the trace id is recorded (records join on it).
  var trace = '';
  var traceparent = str('request.header.traceparent').toLowerCase();
  var tp = traceparent.match(/^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$/);
  if (tp && !/^0+$/.test(tp[1])) {
    trace = '"trace_id":' + q(tp[1]) + ',';
  }
  context.setVariable('usage.trace_fields', trace);

  // First authenticated principal among the principal_ref variables ("anonymous" = none).
  var principal = '';
  var principalRefs = prop('principal_ref').split(',');
  for (var r = 0; r < principalRefs.length && !principal; r++) {
    var candidate = str(principalRefs[r].replace(/^\s+|\s+$/g, ''));
    if (candidate && candidate !== 'anonymous') {
      principal = clip(candidate, 512);
    }
  }
  context.setVariable(
    'usage.caller_fields',
    principal
      ? '"caller":{"principal":' + q(principal) + ',"verified":true},'
      : '"caller":{"verified":false},'
  );

  var workload = str(prop('workload_ref'));
  if (!workload && prop('workload_from_path') === 'true') {
    var segment = str('proxy.pathsuffix').match(/^\/([A-Za-z0-9][A-Za-z0-9_.-]*)/);
    workload = segment ? segment[1].replace(/:.*$/, '') : '';
  }
  var workloadFields = '';
  if (workload) {
    workloadFields = '"workload":{"name":' + q(clip(workload, 128));
    var runtime = prop('workload_runtime');
    if (/^(agent_engine|cloud_run|gke|external)$/.test(runtime)) {
      workloadFields += ',"runtime":' + q(runtime);
    }
    workloadFields += ',"cloud":"gcp"},';
  }
  context.setVariable('usage.workload_fields', workloadFields);

  // In the DefaultFaultRule (phase=fault) the outcome comes from the fault; elsewhere from the
  // response, so a continueOnError policy failure never turns a served request into an error.
  var inFault = prop('phase') === 'fault';
  var code = 0;
  var codeVars = inFault ? ['error.status.code', 'message.status.code'] : ['response.status.code'];
  for (var c = 0; c < codeVars.length && !code; c++) {
    var parsed = Number(context.getVariable(codeVars[c]));
    if (parsed >= 100 && parsed <= 599) {
      code = parsed;
    }
  }
  var fault = inFault ? str('fault.name') || 'fault' : '';
  var failed = inFault || code >= 400;

  var tool = clip(str(prop('tool_ref')), 256);
  var toolFields = '';
  if (tool) {
    toolFields = '"tool":{"name":' + q(tool);
    var server = clip(str(prop('tool_server_ref')), 256);
    if (server) {
      toolFields += ',"server":' + q(server);
    }
    toolFields += ',"status":' + q(failed ? 'error' : 'ok') + '},';
  }
  context.setVariable('usage.tool_fields', toolFields);

  var mode = prop('gen_ai') || 'tokens';
  var input = count('llm.promptTokenCount');
  var candidates = count('llm.candidatesTokenCount');
  var reasoning = count('llm.thoughtsTokenCount');
  var cached = count('llm.cachedTokenCount');
  var requestModel = clip(str(prop('request_model_ref')), 256);
  var responseModel = clip(str('llm.model'), 256);
  var hasTokens = input != null || candidates != null || reasoning != null;
  var genAi = [];
  if (mode === 'always' || (mode === 'tokens' && hasTokens)) {
    if (prop('gen_ai_system')) {
      genAi.push('"system":' + q(prop('gen_ai_system')));
    }
    if (prop('operation_name')) {
      genAi.push('"operation.name":' + q(prop('operation_name')));
    }
    if (requestModel || (mode === 'always' && !responseModel)) {
      genAi.push('"request.model":' + q(requestModel || 'unknown'));
    }
    if (responseModel) {
      genAi.push('"response.model":' + q(responseModel));
    }
    if (input != null) {
      genAi.push('"usage.input_tokens":' + input);
    }
    if (candidates != null || reasoning != null) {
      // OTel GenAI: output_tokens includes reasoning tokens; Gemini reports them apart.
      genAi.push('"usage.output_tokens":' + ((candidates || 0) + (reasoning || 0)));
    }
    if (cached != null) {
      genAi.push('"usage.cached_tokens":' + cached);
    }
    if (reasoning != null) {
      genAi.push('"usage.reasoning_tokens":' + reasoning);
    }
  }
  context.setVariable('usage.gen_ai_fields', genAi.length ? '"gen_ai":{' + genAi.join(',') + '},' : '');

  var outcome = '"http":{"request.method":' + q(clip(str('request.verb') || 'UNKNOWN', 16));
  if (code) {
    outcome += ',"response.status_code":' + code;
  }
  outcome += '},';
  if (code || fault) {
    outcome += '"status":' + q(failed ? 'error' : 'ok') + ',';
  }
  if (failed) {
    outcome += '"error_type":' + q(clip(fault || 'http_' + code, 256)) + ',';
  }
  context.setVariable('usage.outcome_fields', outcome);
})();
