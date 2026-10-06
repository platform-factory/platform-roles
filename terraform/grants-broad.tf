# APPLY A ONLY. The built-in roles Crossplane's identity holds, granted to the
# new engine as well, so that the engine swap's first cluster session and its
# rollback run on exactly Crossplane's permissions (ADR-0018 §4).
#
# Why both at once: Config Connector's SQLInstance controller treats ANY error
# from instances.get, a 403 included, as "not found", and goes on to create
# (ADR-0018, Context). A permission missing from the custom roles would look
# like the very thing C-29 exists to catch, a durable resource the new engine
# failed to adopt. So the engine changes first, with its permissions held
# equal, and the permissions change after, on their own.
#
# APPLY B deletes this file: one PR, one apply, with the cluster down, after
# the first session's audit log has been read for the write permissions the
# engine really used. That return build is C-31's test. Reverting it is one
# apply of this file.
#
# Each custom role must first be shown to be a subset of the built-in role
# below that it replaces (`gcloud iam roles describe`), or the first session
# does not start: only then do the custom grants add nothing.

resource "google_project_iam_member" "engine_broad_cloudsql_admin" {
  project = local.project
  role    = "roles/cloudsql.admin"
  member  = local.engine
}

resource "google_project_iam_member" "engine_broad_artifactregistry_admin" {
  project = local.project
  role    = "roles/artifactregistry.admin"
  member  = local.engine
}

resource "google_project_iam_member" "engine_broad_service_account_admin" {
  project = local.project
  role    = "roles/iam.serviceAccountAdmin"
  member  = local.engine
}

# Conditioned exactly as in grants.tf and in layer 0: an unconditioned
# projectIamAdmin grant would void the condition (ADR-0018 §3).
resource "google_project_iam_member" "engine_broad_project_iam_admin" {
  project = local.project
  role    = "roles/resourcemanager.projectIamAdmin"
  member  = local.engine

  condition {
    title       = "only-cloudsql-connect-roles"
    description = "Limits this identity to granting or revoking exactly roles/cloudsql.client and roles/cloudsql.instanceUser on the project allow policy."
    expression  = "api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly(['roles/cloudsql.client','roles/cloudsql.instanceUser'])"
  }
}
