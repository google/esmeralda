/**
 * Copyright 2026 Google LLC
 * Licensed under the Apache License, Version 2.0.
 *
 * Authorizes one agent -> model call through the LLM gateway, after VJ-Verify-Caller-Token
 * has verified the caller's Google-signed ID token (signature, issuer, audience, expiry).
 *
 *  1. Caller principal: the verified `email` claim (when email_verified), else `sub` (an Agent
 *     Identity token's spiffe:// subject in its principal:// form).
 *     Fail closed: the principal (or its email / subject) must be in `allowed_callers`.
 *     `caller_workloads` maps it to the calling agent's name (llmgw.caller.workload).
 *  2. Path: only Vertex AI publisher-model generateContent, streamGenerateContent and
 *     countTokens (POST) on `/{v1|v1beta1}/projects/{p}/locations/{l}/publishers/google/models/{m}`.
 *  3. Target: the project, location and model must be allowed (`allowed_projects`,
 *     `allowed_locations`, `allowed_models`; `*` and `prefix*` patterns are accepted for models).
 *     The southbound host is derived from the allowed location only, so the caller can't
 *     steer the gateway to any other host.
 *
 * Reads the kvm-llm-gateway values that KVM-Get-LLM-Gateway-Config assigned to llmgw.*;
 * sets llmgw.denied.reason (empty when allowed), llmgw.caller.principal, llm.* request
 * attributes and llm.target.url. Never reads the request body.
 */
(function () {
  function str(name) {
    var value = context.getVariable(name);
    return value == null ? '' : String(value).trim();
  }

  function list(name) {
    var out = [];
    var parts = str(name).split(',');
    for (var i = 0; i < parts.length; i++) {
      var item = parts[i].replace(/^\s+|\s+$/g, '');
      if (item) {
        out.push(item);
      }
    }
    return out;
  }

  function matches(patterns, value) {
    for (var i = 0; i < patterns.length; i++) {
      var pattern = patterns[i];
      if (pattern === '*' || pattern === value) {
        return true;
      }
      if (pattern.charAt(pattern.length - 1) === '*' && value.indexOf(pattern.slice(0, -1)) === 0) {
        return true;
      }
    }
    return false;
  }

  var jwt = 'jwt.VJ-Verify-Caller-Token.';
  var subject = str(jwt + 'claim.subject') || str(jwt + 'decoded.claim.sub');
  // An Agent Identity ID token's subject is the agent's SPIFFE id (spiffe://<trust domain>/...);
  // the catalog, the principal_teams rows and the usage views use its principal:// form.
  if (subject.indexOf('spiffe://') === 0) {
    subject = 'principal://' + subject.substring('spiffe://'.length);
  }
  var email = str(jwt + 'claim.email') || str(jwt + 'decoded.claim.email');
  var emailVerified = (str(jwt + 'claim.email_verified') || str(jwt + 'decoded.claim.email_verified')).toLowerCase();
  if (emailVerified !== 'true') {
    email = '';
  }
  var principal = email || subject;
  context.setVariable('llmgw.caller.principal', principal);
  // DC-capture-llm-metrics reports the verified caller in the dc_consumer_app collector.
  context.setVariable('consumer.app.name', principal || 'anonymous');

  // The calling agent's name (kvm caller_workloads: "<principal>=<agent>,..."): the usage record's
  // workload.name, so the views join it with the agent's own llm.call records (same trace_id).
  var workloads = list('llmgw.caller_workloads');
  var workload = '';
  for (var w = 0; w < workloads.length && principal; w++) {
    var eq = workloads[w].lastIndexOf('=');
    if (eq > 0 && workloads[w].substring(0, eq) === principal) {
      workload = workloads[w].substring(eq + 1);
    }
  }
  context.setVariable('llmgw.caller.workload', workload);

  var reason = '';
  // Fail closed: no principal, or a principal outside the allowlist, is rejected. No wildcard.
  var callers = list('llmgw.allowed_callers');
  var callerAllowed = false;
  for (var c = 0; c < callers.length && principal; c++) {
    if (callers[c] === principal || callers[c] === email || callers[c] === subject) {
      callerAllowed = true;
    }
  }
  if (!callerAllowed) {
    reason = 'caller_not_allowed';
  }

  var suffix = str('proxy.pathsuffix');
  var path = suffix.match(
    /^\/(v1|v1beta1)\/projects\/([a-z0-9][a-z0-9-]{0,62})\/locations\/([a-z0-9-]{1,32})\/publishers\/google\/models\/([A-Za-z0-9][A-Za-z0-9._@-]{0,127}):(generateContent|streamGenerateContent|countTokens)$/
  );
  if (!path || str('request.verb').toUpperCase() !== 'POST') {
    context.setVariable('llmgw.denied.reason', reason || 'unsupported_path');
    return;
  }

  var project = path[2];
  var location = path[3];
  var model = path[4];
  var method = path[5];
  context.setVariable('llm.request.model', model);
  context.setVariable('llm.project', project);
  context.setVariable('llm.location', location);
  context.setVariable('llm.method', method);
  context.setVariable('llm.stream', method === 'streamGenerateContent');

  if (
    !reason &&
    (list('llmgw.allowed_projects').indexOf(project) === -1 ||
      list('llmgw.allowed_locations').indexOf(location) === -1 ||
      !matches(list('llmgw.allowed_models'), model))
  ) {
    reason = 'target_not_allowed';
  }

  var host = location + '-aiplatform.googleapis.com';
  if (location === 'global') {
    host = 'aiplatform.googleapis.com';
  } else if (location === 'us' || location === 'eu') {
    host = 'aiplatform.' + location + '.rep.googleapis.com';
  }
  context.setVariable('llm.target.url', 'https://' + host + suffix);
  context.setVariable('llmgw.denied.reason', reason);
})();
