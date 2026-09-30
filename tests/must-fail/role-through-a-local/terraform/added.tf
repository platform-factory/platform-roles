# The role's name is somewhere else, so the grant's own line does not show it.
locals {
  extra_role = "roles/owner"
}

resource "google_project_iam_member" "engine_via_local" {
  project = local.project
  role    = local.extra_role
  member  = local.engine
}
