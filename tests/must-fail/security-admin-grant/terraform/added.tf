# roles/iam.securityAdmin can set the project's policy, and is on no list.
resource "google_project_iam_member" "engine_security_admin" {
  project = local.project
  role    = "roles/iam.securityAdmin"
  member  = local.engine
}
