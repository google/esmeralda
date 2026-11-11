-- vw_principal_teams: the effective principal -> team mapping read by vw_usage_normalized.
--
--   principal_teams table   rows written by tools (the catalog compiler; source = catalog or the tool)
--   Terraform rows          var.principal_teams of the governance module (source = terraform)
-- Columns: principal, principal_type, team, source. principal_type = gcp_project rows key on a
-- project ID; every other type keys on caller.principal as producers write it.
SELECT principal, principal_type, team, source
FROM `${dataset_id}.principal_teams`
%{ for r in rows ~}
UNION ALL
SELECT ${jsonencode(r.principal)}, ${jsonencode(r.principal_type)}, ${jsonencode(r.team)}, ${jsonencode(r.source)}
%{ endfor ~}
