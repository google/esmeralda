/**
 * Copyright 2026 Google LLC
 * Licensed under the Apache License, Version 2.0.
 *
 * Maps partner-facing agent paths (/cx-mortgage-orchestrator:streamQuery, etc.)
 * to the deployed Vertex AI Reasoning Engine resource path when
 * orchestrator_engine_id is configured in kvm-partner-agents, or falls back to
 * /v1/agents on the southbound entrypoint target server.
 */
(function () {
  var engineId = String(context.getVariable('agent.orchestrator.engine_id') || '').trim();
  var suffix = String(context.getVariable('proxy.pathsuffix') || '');

  if (engineId) {
    context.setVariable('target.copy.pathsuffix', false);
    context.setVariable('target.copy.queryparams', true);
    if (
      suffix.indexOf(':streamQuery') !== -1 ||
      suffix.indexOf('/api/stream_reasoning_engine') !== -1
    ) {
      context.setVariable('esmeralda.target.path', '/v1beta1/' + engineId + ':streamQuery');
    } else if (suffix.indexOf(':query') !== -1) {
      context.setVariable('esmeralda.target.path', '/v1beta1/' + engineId + ':query');
    } else {
      var rest = suffix.replace(/^\/cx-mortgage-orchestrator/, '');
      context.setVariable('esmeralda.target.path', '/v1beta1/' + engineId + rest);
    }
  } else {
    context.setVariable('esmeralda.target.path', '/v1/agents');
  }
})();
