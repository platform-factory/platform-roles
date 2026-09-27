# One more Terraform layer for platform-bootstrap's purposes (ADR-0018 §1): a
# directory with its own state that outlives every cluster, applied by hand,
# never by cycle.sh. It lives in its own repo only because security, not the
# platform team, approves it (ADR-0001).
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source = "hashicorp/google"
      # The same pin as every platform-bootstrap layer.
      version = "~> 7.42"
    }
  }

  # The bucket is platform-bootstrap's state bucket, supplied at init:
  #   terraform init -backend-config="bucket=<project>-tfstate"
  # Its own prefix, so nothing here can touch a platform-bootstrap layer's
  # state.
  backend "gcs" {
    prefix = "platform-roles"
  }
}
