# An authoritative binding instead of an additive member. It would also
# remove every other holder of the role.
resource "google_project_iam_binding" "engine_owner" {
  project = local.project
  role    = "roles/owner"
  members = [local.engine]
}
