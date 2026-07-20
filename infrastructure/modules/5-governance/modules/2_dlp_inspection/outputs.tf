output "inspect_template_id" {
  value       = google_data_loss_prevention_inspect_template.pii_redaction.id
  description = "Cloud DLP inspection template resource ID"
}
