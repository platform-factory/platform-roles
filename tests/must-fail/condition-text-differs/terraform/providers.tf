# Reads platform-bootstrap layer 0's outputs, the way layer 1 does: the
# project, and the engine's service account. Layer 0 says "this identity
# exists"; this repo says "this is everything it may do" (ADR-0018 §1).
data "terraform_remote_state" "foundation" {
  backend = "gcs"

  config = {
    bucket = "${var.project_id}-tfstate"
    prefix = "0-foundation"
  }
}

provider "google" {
  project = data.terraform_remote_state.foundation.outputs.project_id
}

variable "project_id" {
  description = "The project platform-bootstrap's 0-foundation created. Needed only to find its state bucket; every other fact is read from that state."
  type        = string
  default     = "platform-factory-ref"
}

locals {
  project = data.terraform_remote_state.foundation.outputs.project_id
  engine  = "serviceAccount:${data.terraform_remote_state.foundation.outputs.config_connector_service_account_email}"
}
