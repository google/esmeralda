# Pin the Cloud Run revision to the image *digest* the tag points to right now.
# Cloud Run resolves a tag only when a revision is created, so a template that keeps saying
# ":dev-latest" never rolls out a rebuilt image. With the digest in the template, every rebuild
# changes the plan and Terraform creates a new revision (same pattern as the agent modules).
# Images already given by digest, or not in Artifact Registry (e.g. Docker Hub), are used as-is.
locals {
  image_input      = var.kong_image
  image_resolvable = can(regex("^[a-z0-9-]+-docker\\.pkg\\.dev/[^/]+/[^/]+/[^/@]+:[^/@]+$", local.image_input))
  image_parts      = split("/", local.image_input)
  image_name_tag   = local.image_resolvable ? local.image_parts[3] : ""
}

data "google_artifact_registry_docker_image" "image" {
  count         = local.image_resolvable ? 1 : 0
  project       = local.image_parts[1]
  location      = replace(local.image_parts[0], "-docker.pkg.dev", "")
  repository_id = local.image_parts[2]
  image_name    = local.image_name_tag
}

locals {
  # e.g. us-central1-docker.pkg.dev/<proj>/<repo>/corporate-email@sha256:...
  image_by_digest = local.image_resolvable ? "${join("/", slice(local.image_parts, 0, 3))}/${split(":", local.image_name_tag)[0]}@${split("@", data.google_artifact_registry_docker_image.image[0].name)[1]}" : local.image_input
}
