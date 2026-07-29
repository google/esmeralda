output "sink_writer_identities" {
  value       = { for k, v in google_logging_project_sink.central_sinks : k => v.writer_identity }
  description = "Map of log sink writer service account identities per project"
}
