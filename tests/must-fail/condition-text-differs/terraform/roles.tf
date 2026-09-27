# One project-level custom role per file in ../roles/. The file name is the
# role ID, so a reviewer reads one file per role and nothing else.
#
# A role ID cannot be changed and may hold only letters, digits, underscores
# and periods: no hyphen. Deleting a role is a soft delete; its ID cannot be
# reused for up to 44 days (ADR-0018, Context).
locals {
  role_files = fileset("${path.module}/../roles", "*.yaml")
  roles = {
    for file in local.role_files :
    trimsuffix(file, ".yaml") => yamldecode(file("${path.module}/../roles/${file}"))
  }
}

resource "google_project_iam_custom_role" "engine" {
  for_each = local.roles

  project     = local.project
  role_id     = each.key
  title       = each.value.title
  description = each.value.description
  stage       = each.value.stage
  permissions = each.value.includedPermissions

  # Removing a role file should fail the apply rather than soft-delete a role
  # the engine holds: a deleted role turns every engine call into a 403 while
  # its grants still show in the policy. That this argument does so is
  # [unverified]: it is in provider 7.42.0 and its behaviour was read, not run
  # (ADR-0018, "Still unverified").
  deletion_policy = "PREVENT"
}
