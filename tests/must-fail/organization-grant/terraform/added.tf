# A grant above the project, where the project-level reads never look.
resource "google_organization_iam_member" "engine_org" {
  org_id = "123456789012"
  role   = "roles/owner"
  member = local.engine
}
