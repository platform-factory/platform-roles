# Every grant the engine holds, custom or built-in, is in this repo, so one
# place shows security all of it (ADR-0018 §1). One plain block per grant, so
# a grant is a visible line in a diff.
#
# All additive google_project_iam_member, never _binding or _policy, which are
# authoritative and would clobber the organisation's and GKE's own bindings.

resource "google_project_iam_member" "engine_sql" {
  project = local.project
  role    = google_project_iam_custom_role.engine["platformEngineSql"].id
  member  = local.engine
}

resource "google_project_iam_member" "engine_registry" {
  project = local.project
  role    = google_project_iam_custom_role.engine["platformEngineRegistry"].id
  member  = local.engine
}

resource "google_project_iam_member" "engine_service_accounts" {
  project = local.project
  role    = google_project_iam_custom_role.engine["platformEngineServiceAccounts"].id
  member  = local.engine
}

# The one conditioned grant (ADR-0018 §3). The condition is copied BYTE FOR
# BYTE from platform-bootstrap's layer 0 (iam.tf,
# crossplane_provider_project_iam_admin), so ADR-0013 §6's decision stands and
# only the role carrying it changes. The repo's check compares the two.
#
# Three rules that keep it meaningful:
#   - This role stands alone here. Merged with another role, a repository grant
#     would simply fail (Artifact Registry does not recognise the condition's
#     attribute) and a service-account grant would drag
#     roles/iam.workloadIdentityUser into the allow-list.
#   - The allow-list stays the two built-in roles granted TO TENANTS and never
#     gains a custom role, which Google warns against for any role the limited
#     admin could modify.
#   - The identity must hold NO unconditioned grant of any role containing
#     resourcemanager.projects.setIamPolicy. One stray grant, anywhere, voids
#     the condition silently. The check refuses one here; the pre-session
#     reads look for one made by hand.
#
# Terraform treats the condition's title, description and expression as part
# of the binding's identity: editing any of them is a destroy and a create.
resource "google_project_iam_member" "engine_project_iam" {
  project = local.project
  role    = google_project_iam_custom_role.engine["platformEngineProjectIam"].id
  member  = local.engine

  condition {
    title       = "only-cloudsql-connect-roles"
    description = "Limits this identity to granting or revoking exactly roles/cloudsql.client and roles/cloudsql.instanceUser on the project allow policy."
    expression  = "api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly(['roles/cloudsql.client','roles/cloudsql.instanceUser'])"
  }
}

# The fifth role stays built-in for M2b: read-only, and the one Cloud SQL
# prerequisite whose need Google's pages leave unstated. The honest headline
# is "four of five"; narrowing it is the first measured change after M2b
# closes (ADR-0018 §2).
resource "google_project_iam_member" "stray_project_iam_admin" {
  project = local.project
  role    = "roles/resourcemanager.projectIamAdmin"
  member  = local.engine
}

resource "google_project_iam_member" "engine_compute_viewer" {
  project = local.project
  role    = "roles/compute.viewer"
  member  = local.engine
}
