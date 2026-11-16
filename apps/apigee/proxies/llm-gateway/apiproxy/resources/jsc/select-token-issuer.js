/**
 * Copyright 2026 Google LLC
 * Licensed under the Apache License, Version 2.0.
 *
 * Picks the issuer and JWKS that VJ-Verify-Caller-Token checks the caller's ID token against,
 * from the issuer DJ-Decode-Caller-Token read (unverified) and the issuers this gateway trusts:
 *
 *  - an agent's Agent Identity ID token: issuer = its trust domain on Security Token Service
 *    (https://sts.googleapis.com/v1/{organizations|projects}/<n>/locations/global/
 *    workloadIdentityPools/agents.global.<org|proj>-<n>.system.id.goog), JWKS = <issuer>/openid/jwks.
 *    Only issuers listed in kvm-llm-gateway `agent_issuers` are accepted;
 *  - anything else: Google's issuer (https://accounts.google.com) and JWKS, i.e. a service account
 *    ID token (the test VM's probes). A token from any other issuer then fails VerifyJWT (401).
 *
 * The JWKS URI is never built from an issuer outside the allowlist, so a caller can't point the
 * gateway at a key set it controls. Sets llmgw.token.issuer, llmgw.token.jwks_uri and
 * llmgw.token.kind (agent_identity | google).
 */
(function () {
  var GOOGLE_ISSUER = 'https://accounts.google.com';
  var GOOGLE_JWKS = 'https://www.googleapis.com/oauth2/v3/certs';

  function str(name) {
    var value = context.getVariable(name);
    return value == null ? '' : String(value).trim();
  }

  var dj = 'jwt.DJ-Decode-Caller-Token.';
  var issuer = str(dj + 'claim.issuer') || str(dj + 'decoded.claim.iss');

  var trusted = str('llmgw.agent_issuers').split(',');
  var agent = false;
  for (var i = 0; i < trusted.length && issuer; i++) {
    var candidate = trusted[i].replace(/^\s+|\s+$/g, '');
    if (candidate && candidate.indexOf('https://sts.googleapis.com/') === 0 && candidate === issuer) {
      agent = true;
    }
  }

  context.setVariable('llmgw.token.kind', agent ? 'agent_identity' : 'google');
  context.setVariable('llmgw.token.issuer', agent ? issuer : GOOGLE_ISSUER);
  context.setVariable('llmgw.token.jwks_uri', agent ? issuer + '/openid/jwks' : GOOGLE_JWKS);
})();
