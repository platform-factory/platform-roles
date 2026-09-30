# A module can hold any resource, and this check would not read inside it.
module "more_grants" {
  source = "./more"
}
