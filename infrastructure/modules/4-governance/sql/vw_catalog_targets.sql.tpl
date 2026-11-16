-- vw_catalog_targets: the effective catalog target metadata read by usage analytics and esmeralda-ui.
--
--   catalog_targets table   rows written by the runtime catalog store (layer 5 iap-egress; source = catalog)
-- Columns: target, kind, owner_team, unit, endpoint, token, probe, callers_json, source.
SELECT
  target,
  kind,
  owner_team,
  unit,
  endpoint,
  token,
  probe,
  callers_json,
  source
FROM `${dataset_id}.catalog_targets`
