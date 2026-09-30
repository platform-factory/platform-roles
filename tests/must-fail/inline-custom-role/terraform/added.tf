# A second custom role, written here instead of as a file in roles/. None of
# the role-file rules would read its permissions.
resource "google_project_iam_custom_role" "extra" {
  project     = local.project
  role_id     = "platformEngineExtra"
  title       = "Platform engine - extra"
  permissions = ["cloudsql.instances.delete", "iam.roles.update"]
}
