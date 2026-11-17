variable "project_id" {
  description = "Project ID of the agent project (the project that hosts the Agent Runtime engines)."
  type        = string
}

variable "org_id" {
  description = "Optional organization ID (numeric, or organizations/<n>; ESMERALDA_ORG_ID). For a project under a folder it is used instead of the folder lookup, so resourcemanager.folders.get is not needed. Ignored for a project whose parent is the organization (its own org_id wins) and for a project with no organization."
  type        = string
  default     = ""
}
