/**
 * Copyright 2026 Google LLC
 * Licensed under the Apache License, Version 2.0.
 * Adapted from github.com/ricardosmh/adk-apigee-multiagent-demo (Apache-2.0).
 *
 * Extracts LLM token usage and model name across three dialects:
 *   1. ADK :streamQuery / A2A message/stream (usage_metadata.prompt_token_count, etc.)
 *   2. Vertex AI Gemini (usageMetadata.promptTokenCount, etc.)
 *   3. Anthropic Messages API (message_start + message_delta)
 * Also supports non-streaming JSON responses when invoked on response.content.
 */
(function () {
  var raw = context.getVariable('response.event.current.content');
  var contentType = String(context.getVariable('response.header.Content-Type') || '');
  if (!raw && contentType.toLowerCase().indexOf('text/event-stream') === -1) {
    raw = context.getVariable('response.content');
  }

  context.setVariable('isFinal', false);
  if (!raw) {
    return;
  }

  function parsePayloadStrings(input) {
    var text = String(input).trim();
    if (!text) {
      return [];
    }
    if (text.charAt(0) === '{') {
      try {
        JSON.parse(text);
        return [text];
      } catch (_ndjsonErr) {
        var ndLines = text.split(/\r?\n/);
        var ndPayloads = [];
        for (var n = 0; n < ndLines.length; n++) {
          var nl = ndLines[n].trim();
          if (nl.charAt(0) === '{') {
            ndPayloads.push(nl);
          }
        }
        if (ndPayloads.length > 0) {
          return ndPayloads;
        }
      }
    }
    var payloads = [];
    var blocks = text.split(/\r?\n\r?\n/);
    for (var b = 0; b < blocks.length; b++) {
      var lines = blocks[b].split(/\r?\n/);
      var dataLines = [];
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i].replace(/^\s+/, '');
        if (line.indexOf('data:') === 0) {
          var datum = line.substring(5).replace(/^\s+/, '');
          if (datum && datum !== '[DONE]') {
            try {
              JSON.parse(datum);
              payloads.push(datum);
            } catch (_singleErr) {
              dataLines.push(datum);
            }
          }
        }
      }
      if (dataLines.length > 0) {
        var joined = dataLines.join('').trim();
        if (joined && joined !== '[DONE]') {
          payloads.push(joined);
        }
      }
    }
    return payloads;
  }

  function findAdkUsageMetadata(obj) {
    if (!obj || typeof obj !== 'object') {
      return null;
    }
    if (obj.usage_metadata && typeof obj.usage_metadata === 'object') {
      return obj.usage_metadata;
    }
    if (obj.result && typeof obj.result === 'object') {
      var r = obj.result;
      if (r.usage_metadata && typeof r.usage_metadata === 'object') {
        return r.usage_metadata;
      }
      if (r.metadata && r.metadata.usage_metadata && typeof r.metadata.usage_metadata === 'object') {
        return r.metadata.usage_metadata;
      }
      if (r.status && r.status.message && typeof r.status.message === 'object') {
        var msg = r.status.message;
        if (msg.usage_metadata && typeof msg.usage_metadata === 'object') {
          return msg.usage_metadata;
        }
        if (msg.metadata && msg.metadata.usage_metadata && typeof msg.metadata.usage_metadata === 'object') {
          return msg.metadata.usage_metadata;
        }
      }
    }
    return null;
  }

  function findModel(obj) {
    if (!obj || typeof obj !== 'object') {
      return null;
    }
    if (obj.model_version) return obj.model_version;
    if (obj.modelVersion) return obj.modelVersion;
    if (obj.model) return obj.model;
    if (obj.message && obj.message.model) return obj.message.model;
    if (obj.result && typeof obj.result === 'object') {
      if (obj.result.model_version) return obj.result.model_version;
      if (obj.result.modelVersion) return obj.result.modelVersion;
      if (obj.result.model) return obj.result.model;
      if (obj.result.metadata && obj.result.metadata.model) return obj.result.metadata.model;
      if (
        obj.result.status &&
        obj.result.status.message &&
        obj.result.status.message.metadata &&
        obj.result.status.message.metadata.model
      ) {
        return obj.result.status.message.metadata.model;
      }
    }
    return null;
  }

  try {
    var payloads = parsePayloadStrings(raw);
    for (var idx = 0; idx < payloads.length; idx++) {
      var p = JSON.parse(payloads[idx]);
      var detectedModel = findModel(p);
      if (detectedModel) {
        context.setVariable('llm.model', String(detectedModel));
      }

      var inTok = null;
      var outTok = null;
      var totalTok = null;
      var fin = false;

      var adkUsage = findAdkUsageMetadata(p);
      var geminiUsage = p.usageMetadata || (p.result && p.result.usageMetadata);

      if (adkUsage) {
        if (adkUsage.prompt_token_count != null || adkUsage.input_tokens != null) {
          inTok = Number(
            adkUsage.prompt_token_count != null ? adkUsage.prompt_token_count : adkUsage.input_tokens
          );
        }
        if (adkUsage.candidates_token_count != null || adkUsage.output_tokens != null) {
          outTok = Number(
            adkUsage.candidates_token_count != null
              ? adkUsage.candidates_token_count
              : adkUsage.output_tokens
          );
        }
        if (inTok != null || outTok != null) {
          var promptVal = inTok != null ? inTok : 0;
          var candVal = outTok != null ? outTok : 0;
          totalTok = promptVal + candVal;
          inTok = promptVal;
          outTok = candVal;
          fin = true;
        }
      } else if (geminiUsage) {
        if (geminiUsage.promptTokenCount != null) {
          inTok = Number(geminiUsage.promptTokenCount);
        }
        if (geminiUsage.candidatesTokenCount != null) {
          outTok = Number(geminiUsage.candidatesTokenCount);
        }
        if (inTok != null || outTok != null) {
          var gPrompt = inTok != null ? inTok : 0;
          var gCand = outTok != null ? outTok : 0;
          totalTok = gPrompt + gCand;
          inTok = gPrompt;
          outTok = gCand;
          var hasCandidates = p.candidates && p.candidates.length > 0;
          var hasFinishReason = hasCandidates && Boolean(p.candidates[0].finishReason);
          if (!hasCandidates || hasFinishReason) {
            fin = true;
          }
        }
      } else if (p.type === 'message_start' && p.message && p.message.usage) {
        if (p.message.usage.input_tokens != null) {
          var startIn = Number(p.message.usage.input_tokens);
          context.setVariable('anthInput', startIn);
          context.setVariable('llm.promptTokenCount', startIn);
        }
      } else if (p.type === 'message_delta' && p.usage) {
        outTok = Number(p.usage.output_tokens != null ? p.usage.output_tokens : 0);
        var savedIn =
          p.usage.input_tokens != null
            ? p.usage.input_tokens
            : context.getVariable('llm.promptTokenCount');
        if (savedIn == null) {
          savedIn = context.getVariable('anthInput');
        }
        inTok = Number(savedIn != null ? savedIn : 0);
        totalTok = inTok + outTok;
        fin = true;
      }

      function toIntVar(val) {
        var rounded = Math.round(Number(val));
        if (typeof java !== 'undefined' && java.lang && java.lang.Integer) {
          return java.lang.Integer.valueOf(rounded);
        }
        return rounded;
      }

      if (inTok != null) {
        context.setVariable('llm.promptTokenCount', toIntVar(inTok));
      }
      if (outTok != null) {
        context.setVariable('llm.candidatesTokenCount', toIntVar(outTok));
      }
      if (totalTok != null) {
        context.setVariable('llm.totalTokenCount', toIntVar(totalTok));
      }
      if (fin) {
        context.setVariable('isFinal', true);
      }
    }
  } catch (_err) {
    context.setVariable('js_error_occurred', true);
  }
})();
