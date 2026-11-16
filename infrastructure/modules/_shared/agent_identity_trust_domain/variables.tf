variable "project_id" {
  description = "Project ID of the agent project (the project that hosts the Agent Runtime engines)."
  type        = string
}

variable "org_id" {
  description = "Optional organization ID, used only for a project under a folder whose organization the folder lookup did not return."
  type        = string
  default     = ""
}
