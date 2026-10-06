#!/usr/bin/env python3
"""The intent gate for platform-roles (ADR-0018 §6). No cloud credential needed.

Why it exists
-------------
This repo is the whole list of what the platform's cloud engine may do. What
the list leaves out is the point: no durable delete is the third deletion
lock, and no role administration is what keeps the engine from widening its
own roles. A reviewer can miss one added line; this check does not. It is the
intent gate. The reality gate is the operator's reads before each session
(README, "Before each session"), because a check on files cannot see a grant
made by hand.

What it refuses
---------------
  role files (roles/*.yaml):
    [role-id]            the file name is not a valid custom role ID (letters,
                         digits, underscores, periods; no hyphen)
    [shape]              keys other than title, description, stage,
                         includedPermissions; a stage other than GA; a title
                         over 100 bytes or a description over 256 (Google
                         counts bytes, and refuses only at apply)
    [durable-delete]     deleting a Cloud SQL instance, database or user, or a
                         registry
    [role-admin]         anything under iam.roles.
    [keys-or-act-as]     a service-account key, or acting as another account
    [project-set-iam]    resourcemanager.projects.setIamPolicy outside
                         platformEngineProjectIam.yaml
  grants (terraform/*.tf), as an ALLOWLIST: what is not named below is refused,
  so a new way to grant something has to be added here, in review, first.
    [resource-kind]      any resource other than a project custom role or a
                         project IAM member (so no _binding or _policy, no
                         grant on the organisation, a folder or another
                         service account); any module; any *.tf.json file,
                         which this check cannot read
    [inline-role]        a custom role that does not take its permissions
                         from a file in roles/, where the rules above apply
    [role-not-listed]    a grant of anything but one of the custom roles in
                         roles/, or a built-in role outside the short list
                         each file may grant (BUILT_IN_ROLES below)
    [member]             a grant to anyone but the engine
    [unconditioned]      a grant of a role that can set the project's policy
                         without the condition: one such grant voids it
                         (ADR-0018 §3)
    [condition-text]     a condition not byte-identical to platform-bootstrap
                         layer 0's

Its limit: it reads Terraform with regular expressions, not a parser. It is a
net for slips in the files a reviewer reads, and it fails closed (a value it
cannot read is refused). How roles.tf builds `local.roles` from roles/ is
still the reviewer's to read.

How to run it
-------------
  python3 scripts/check_roles.py --bootstrap-iam ../platform-bootstrap/layers/0-foundation/iam.tf
  python3 scripts/check_roles.py --self-test --bootstrap-iam <same>
"""

import argparse
import pathlib
import re
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "must-fail"

ROLE_ID = re.compile(r"^[A-Za-z0-9_.]{3,64}$")
ROLE_KEYS = {"title", "description", "stage", "includedPermissions"}
DURABLE_DELETES = {
    "cloudsql.instances.delete",
    "cloudsql.databases.delete",
    "cloudsql.users.delete",
    "artifactregistry.repositories.delete",
}
ACT_AS = {
    "iam.serviceAccounts.actAs",
    "iam.serviceAccounts.getAccessToken",
    "iam.serviceAccounts.getOpenIdToken",
    "iam.serviceAccounts.implicitDelegation",
    "iam.serviceAccounts.signBlob",
    "iam.serviceAccounts.signJwt",
}
PROJECT_SET_IAM = "resourcemanager.projects.setIamPolicy"
PROJECT_IAM_FILE = "platformEngineProjectIam"
# Roles that can set the project's allow policy, and so must be conditioned.
POLICY_SETTING_ROLES = {"roles/resourcemanager.projectIamAdmin", "roles/owner", "roles/editor"}

# Everything terraform/*.tf may create: the custom roles, and grants of roles
# on the project. A grant written any other way is one the rules below would
# never see.
RESOURCE_KINDS = {"google_project_iam_custom_role", "google_project_iam_member"}
# The built-in roles the engine may hold, by the file that may grant them.
# grants-broad.tf exists only between apply A and apply B (ADR-0018 §4), so
# once it is deleted the broad roles cannot come back through another file.
BUILT_IN_ROLES = {
    "grants.tf": {"roles/compute.viewer"},
    "grants-broad.tf": {
        "roles/cloudsql.admin",
        "roles/artifactregistry.admin",
        "roles/iam.serviceAccountAdmin",
        "roles/resourcemanager.projectIamAdmin",
    },
}
# How a grant names one of this repo's custom roles.
CUSTOM_ROLE_REF = re.compile(r'^google_project_iam_custom_role\.engine\["([A-Za-z0-9_.]+)"\]\.id$')
# What the one custom-role block must say, so that every role's permissions
# come from a file in roles/ and pass check_role_files.
CUSTOM_ROLE_BLOCK = {
    "for_each": "local.roles",
    "role_id": "each.key",
    "permissions": "each.value.includedPermissions",
}
ENGINE = "local.engine"


def check_role_files(roles_dir):
    problems = []
    for path in sorted(roles_dir.glob("*.yaml")):
        role_id = path.stem
        where = f"roles/{path.name}"
        if not ROLE_ID.match(role_id):
            problems.append(("role-id", f"{where}: '{role_id}' is not a valid custom role ID"))
        role = yaml.safe_load(path.read_text()) or {}
        if set(role) != ROLE_KEYS:
            problems.append(("shape", f"{where}: keys must be exactly {sorted(ROLE_KEYS)}, found {sorted(role)}"))
        if role.get("stage") != "GA":
            problems.append(("shape", f"{where}: stage must be GA, found {role.get('stage')!r}"))
        if len(str(role.get("title", "")).encode()) > 100:
            problems.append(("shape", f"{where}: title is over 100 bytes"))
        if len(str(role.get("description", "")).encode()) > 256:
            problems.append(("shape", f"{where}: description is over 256 bytes"))
        for permission in role.get("includedPermissions") or []:
            if permission in DURABLE_DELETES:
                problems.append(("durable-delete", f"{where}: {permission} would remove the third deletion lock (ADR-0018 §5)"))
            if permission.startswith("iam.roles."):
                problems.append(("role-admin", f"{where}: {permission}: an identity that can edit custom roles can grant itself anything"))
            if permission.startswith("iam.serviceAccountKeys.") or permission in ACT_AS:
                problems.append(("keys-or-act-as", f"{where}: {permission} would let the engine hold a key or act as another account"))
            if permission == PROJECT_SET_IAM and role_id != PROJECT_IAM_FILE:
                problems.append(("project-set-iam", f"{where}: {permission} belongs only in {PROJECT_IAM_FILE}.yaml, the one conditioned grant"))
    return problems


def blocks(text, kind):
    """Yield (name, body) for each `resource "<kind>" "<name>" { ... }` block."""
    for match in re.finditer(r'resource\s+"%s"\s+"([^"]+)"\s*\{' % re.escape(kind), text):
        depth, i = 1, match.end()
        while depth and i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        yield match.group(1), text[match.end():i - 1]


def condition_fields(body):
    """title, description and expression of a block's condition, or None."""
    match = re.search(r"condition\s*\{(.*?)\n\s*\}", body, re.S)
    if not match:
        return None
    fields = {}
    for key in ("title", "description", "expression"):
        found = re.search(r'\b%s\s*=\s*"((?:[^"\\]|\\.)*)"' % key, match.group(1))
        fields[key] = found.group(1) if found else None
    return fields


def layer0_condition(bootstrap_iam):
    text = bootstrap_iam.read_text()
    for name, body in blocks(text, "google_project_iam_member"):
        if name == "crossplane_provider_project_iam_admin":
            return condition_fields(body)
    raise SystemExit(f"no crossplane_provider_project_iam_admin block in {bootstrap_iam}")


def without_comments(text):
    """Drop whole-line comments, so a rule never trips on a word in one."""
    return re.sub(r"(?m)^\s*(#|//).*$", "", text)


def attribute(body, key):
    """The text after `key =` on its own line in a block, or None."""
    found = re.search(r"(?m)^\s*%s\s*=\s*(.+?)\s*$" % re.escape(key), body)
    return found.group(1) if found else None


def check_grants(terraform_dir, roles_dir, expected_condition):
    problems = []
    role_files = {path.stem for path in roles_dir.glob("*.yaml")}

    for path in sorted(terraform_dir.glob("*.tf.json")):
        problems.append(("resource-kind", f"terraform/{path.name}: Terraform loads *.tf.json files, and this check cannot read them"))

    for path in sorted(terraform_dir.glob("*.tf")):
        text = without_comments(path.read_text())

        # Only the two kinds of resource this repo exists to hold, and no
        # module, which could hold any kind.
        for kind, name in re.findall(r'resource\s+"([^"]+)"\s+"([^"]+)"', text):
            if kind not in RESOURCE_KINDS:
                problems.append(("resource-kind", f"terraform/{path.name}: {name}: a {kind} is not one of {sorted(RESOURCE_KINDS)}; every grant the engine holds is a google_project_iam_member (ADR-0018 §1)"))
        for name in re.findall(r'(?m)^\s*module\s+"([^"]+)"', text):
            problems.append(("resource-kind", f"terraform/{path.name}: module {name}: this root holds plain blocks only, one per grant (ADR-0018 §8)"))

        for name, body in blocks(text, "google_project_iam_custom_role"):
            where = f"terraform/{path.name}: {name}"
            found = {key: attribute(body, key) for key in CUSTOM_ROLE_BLOCK}
            if name != "engine" or found != CUSTOM_ROLE_BLOCK:
                problems.append(("inline-role", f"{where}: a custom role must be a file in roles/, read by the one `engine` block in roles.tf; written here, its permissions skip every role-file rule"))

        for name, body in blocks(text, "google_project_iam_member"):
            where = f"terraform/{path.name}: {name}"
            role = attribute(body, "role") or ""
            custom = CUSTOM_ROLE_REF.match(role)
            built_in = role.strip('"') if re.fullmatch(r'"[^"]*"', role) else None
            if custom:
                listed = custom.group(1) in role_files
            else:
                listed = built_in in BUILT_IN_ROLES.get(path.name, set())
            if not listed:
                problems.append(("role-not-listed", f"{where}: role {role or '<none>'} is neither a custom role in roles/ nor a built-in role {path.name} may grant (BUILT_IN_ROLES in this script)"))
            if attribute(body, "member") != ENGINE:
                problems.append(("member", f"{where}: this repo grants to the engine only (member = {ENGINE})"))

            sets_policy = (custom and custom.group(1) == PROJECT_IAM_FILE) or built_in in POLICY_SETTING_ROLES
            condition = condition_fields(body)
            if sets_policy and condition is None:
                problems.append(("unconditioned", f"{where}: grants a role that can set the project's policy with no condition; one such grant voids the condition (ADR-0018 §3)"))
            if condition is not None and condition != expected_condition:
                problems.append(("condition-text", f"{where}: the condition differs from platform-bootstrap layer 0's; it must be byte-identical (ADR-0018 §3)"))
    return problems


def check_repo(root, expected_condition):
    return check_role_files(root / "roles") + check_grants(root / "terraform", root / "roles", expected_condition)


def self_test(expected_condition):
    """Every fixture must be refused, by the rule its `expect` file names."""
    ok = True
    cases = sorted(p for p in FIXTURES.iterdir() if p.is_dir())
    if not cases:
        print(f"FAIL no fixtures under {FIXTURES}")
        return False
    for case in cases:
        expected = (case / "expect").read_text().strip()
        # A fixture holds only the folder it changes, and only that folder is
        # checked, so the refusal has to come from the change itself.
        problems = []
        if (case / "roles").is_dir():
            problems += check_role_files(case / "roles")
        if (case / "terraform").is_dir():
            problems += check_grants(case / "terraform", REPO / "roles", expected_condition)
        rules = {rule for rule, _ in problems}
        if expected in rules:
            print(f"ok   must-fail/{case.name}: refused by [{expected}]")
        else:
            print(f"FAIL must-fail/{case.name}: expected [{expected}], got {sorted(rules) or 'nothing; it passed'}")
            ok = False
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bootstrap-iam", type=pathlib.Path, required=True,
                        help="platform-bootstrap's layers/0-foundation/iam.tf, whose condition the grants must copy")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    expected = layer0_condition(args.bootstrap_iam)

    if args.self_test:
        sys.exit(0 if self_test(expected) else 1)

    problems = check_repo(REPO, expected)
    for rule, message in problems:
        print(f"::error::[{rule}] {message}")
    if not problems:
        print("ok   every role file and grant passes")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
