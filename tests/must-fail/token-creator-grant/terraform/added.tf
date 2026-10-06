# Acting as another account, granted on that account rather than through a
# permission in a role file.
resource "google_service_account_iam_member" "engine_acts_as" {
  service_account_id = "projects/example-project/serviceAccounts/other@example-project.iam.gserviceaccount.com"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.engine
}
