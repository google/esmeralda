terraform {
  # >= 1.9: variable validations below reference other variables (backends).
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}
