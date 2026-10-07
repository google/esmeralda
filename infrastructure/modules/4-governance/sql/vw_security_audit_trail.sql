-- Security & Compliance Audit Trail SQL View
-- Parses Cloud Audit activity logs to track IAM role modifications, secret access, and Reasoning Engine deployments
SELECT
  timestamp,
  protopayload_auditlog.authenticationInfo.principalEmail AS principal_email,
  protopayload_auditlog.methodName AS method_name,
  protopayload_auditlog.resourceName AS resource_name,
  protopayload_auditlog.serviceName AS service_name,
  COALESCE(protopayload_auditlog.status.message, 'SUCCESS') AS status_message
FROM
  `esmeralda-governance-dev.esmeralda_telemetry_logs_dev.cloudaudit_googleapis_com_activity_*`
WHERE
  protopayload_auditlog.methodName LIKE '%SetIamPolicy%'
  OR protopayload_auditlog.methodName LIKE '%AccessSecretVersion%'
  OR protopayload_auditlog.methodName LIKE '%ReasoningEngine%'
ORDER BY
  timestamp DESC;
