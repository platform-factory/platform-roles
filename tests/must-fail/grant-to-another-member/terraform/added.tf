# This repo is the engine's grants and nobody else's.
resource "google_project_iam_member" "someone_else" {
  project = local.project
  role    = google_project_iam_custom_role.engine["platformEngineSql"].id
  member  = "user:someone@example.com"
}
