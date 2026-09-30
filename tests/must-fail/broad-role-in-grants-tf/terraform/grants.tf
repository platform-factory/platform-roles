# A broad built-in role outside grants-broad.tf: it would survive apply B,
# which deletes that one file (ADR-0018 §4).
resource "google_project_iam_member" "engine_cloudsql_admin" {
  project = local.project
  role    = "roles/cloudsql.admin"
  member  = local.engine
}
