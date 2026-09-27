# platform-roles

Everything the platform's cloud engine may do in Google Cloud, as a list
([ADR-0018](https://github.com/platform-factory/platform-factory-concept/blob/main/docs/adr/0018-engine-permissions-are-a-list-in-platform-roles.md)).
What the list leaves out is the point: the engine cannot delete a database or
a registry, whatever happens in the cluster.

## Why this repo exists

The platform's cloud engine is Config Connector, running as one Google service
account, `config-connector@<project>.iam.gserviceaccount.com`. Given Google's
built-in roles, that account would hold about 270 permissions, including
deleting any database and any registry in the project, the one thing ADR-0015
says automation must never do.

**A custom role is only a named list of permissions that you own.** What is on
the list, the account may do; what is not, Google refuses. So this repo holds
four such lists, with 21 permissions between them, each one traced to the
engine call that needs it. The deletes are left off on purpose, and that
omission is the third lock on a durable resource: Google refuses the call
whether or not anything in the cluster is working.

**The engine never applies this repo.** It does not exist when its roles are
needed, the cluster it runs on is torn down between sessions, and "if a
principal can edit custom roles in a project, they can add any permission to
any custom role in that project" (Google, IAM roles overview). So security
approves every change here, and a person applies it with Terraform.

## The mental model

> platform-bootstrap's layer 0 says **"this identity exists"**.
> This repo says **"this is everything it may do"**.

Layer 0 creates the account and its one Workload Identity binding, and exports
its email. This repo reads that output and turns each file in `roles/` into a
project-level custom role, then grants it. *Every* grant the engine holds,
custom or built-in, is in this repo, so one place shows security all of it.

It is best thought of as one more Terraform layer that lives in its own repo
only because a different group approves it (ADR-0001): its own state prefix in
platform-bootstrap's state bucket, applied by hand after `0-foundation`, never
touched by `cycle.sh`.

## The four roles

| Role (file in `roles/`) | Replaces | Holds | Leaves out, on purpose |
|---|---|---|---|
| `platformEngineSql` | `roles/cloudsql.admin`, 173 permissions | instances create, get, update; databases create, get, update; users create, list | every delete; `users.update` (a password reset is a person's `gcloud` command); export, import, clone, restore |
| `platformEngineRegistry` | `roles/artifactregistry.admin`, about 70 | repositories create, get, update, getIamPolicy, setIamPolicy | repository delete; everything about the images inside |
| `platformEngineServiceAccounts` | `roles/iam.serviceAccountAdmin`, 19 | serviceAccounts create, get, update, delete, getIamPolicy, setIamPolicy | disable, enable, undelete, list, keys, acting as another account. Delete stays: a tenant's account goes when the tenant goes |
| `platformEngineProjectIam` | `roles/resourcemanager.projectIamAdmin`, 9 | projects getIamPolicy, setIamPolicy | everything else. Granted only under an IAM Condition that lets it grant exactly `roles/cloudsql.client` and `roles/cloudsql.instanceUser` |

`roles/compute.viewer` stays built-in for now: it is read-only, and which
compute permission Cloud SQL checks on the caller, if any, is the one thing
Google's pages leave unstated. The honest headline is *four of five*.

## What IAM still does not stop

Kept beside the grants on purpose (ADR-0018, Consequences). These roles stop
**accidents**, a mistake in git that cascades into a cloud delete. They do not
contain an engine under hostile control:

- `iam.serviceAccounts.setIamPolicy` is project-wide. The engine could let
  itself act as any service account in the project.
- `artifactregistry.repositories.setIamPolicy` is as wide, and Artifact
  Registry does not recognise the condition's attribute: the engine could
  grant itself admin on a repository and then delete it, or make one public.
  Such a grant sits on the repository, not the project, which is why the
  fourth read below exists.
- `cloudsql.instances.update` can switch on a public IP. The fix is an
  organisation policy, `constraints/sql.restrictPublicIp`, a named follow-up.
- `cloudsql.users.create` can make a built-in database user, which Cloud SQL
  puts in `cloudsqlsuperuser` by default. No constraint for that was found.

## How it is applied

By a person, from a checkout of `main`, after security has approved the PR:

```sh
cd terraform
terraform init -backend-config="bucket=<project>-tfstate"
terraform plan
terraform apply
```

**Twice, in two steps, and never both changes in one build** (ADR-0018 §4):

- **Apply A**, before any cluster exists, creates the four roles and grants the
  engine both them and the built-in roles Crossplane holds
  (`terraform/grants-broad.tf`). The engine swap's first cluster session and
  its rollback then run on exactly Crossplane's permissions, so every failure
  in them is about the engine, not its roles. Before apply A, each role file is
  shown to be a subset of the built-in role it replaces:

  ```sh
  gcloud iam roles describe roles/cloudsql.admin --format='value(includedPermissions)' \
    | tr ';' '\n' | sort > /tmp/builtin.txt
  sed -n 's/^  - //p' roles/platformEngineSql.yaml | sort | comm -23 - /tmp/builtin.txt
  # prints nothing when the file is a subset; repeat for each file and its role
  ```

- **Apply B** deletes `terraform/grants-broad.tf`: one PR, one apply, with the
  cluster down, after the first session's audit log has been read for the
  permissions the engine actually used. The return build onto the narrow roles
  is claim C-31's test. Reverting it is one apply of that file.

## Before each session: four reads

The check in this repo is the **intent** gate: it reads files. These four
commands are the **reality** gate, because a grant made by hand, or by the
engine itself, is not in any file (ADR-0018 §6, ADR-0019 §5).

1. `terraform plan` says **No changes**.
2. The project's policy shows exactly the grants in this repo for the engine:
   ```sh
   gcloud projects get-iam-policy <project> --flatten=bindings \
     --filter='bindings.members:serviceAccount:config-connector@<project>.iam.gserviceaccount.com' \
     --format='table(bindings.role, bindings.condition.title)'
   ```
3. The engine's own policy shows only its Workload Identity binding (this is
   also what catches a token-creator grant left behind by the desk rehearsal):
   ```sh
   gcloud iam service-accounts get-iam-policy config-connector@<project>.iam.gserviceaccount.com
   ```
4. **Everything the engine can reach, across the organisation.** Policy
   Analyzer looks at every allow policy at or below the organisation, including
   those on a single repository or service account, and through groups:
   ```sh
   gcloud asset analyze-iam-policy --organization=<org-id> \
     --identity=serviceAccount:config-connector@<project>.iam.gserviceaccount.com \
     --expand-roles --analyze-service-account-impersonation
   ```
   The expected answer is exactly the grants in this repo, all on the project.
   Anything else stops the session, and so does a result that says it is
   incomplete (`fullyExplored: false`, or an analysis state other than OK).
   It needs the Cloud Asset API (layer 0 enables it) and, for the operator,
   Cloud Asset Viewer at the organisation, Role Viewer, Service Usage Consumer
   where the call is billed, and the Workspace `groups.read` privilege. Its data
   is best-effort fresh (usually minutes, up to about 36 hours for IAM
   policies), and the organisation gets 20 analysis queries a day without a
   paid Security Command Center tier.

A read is not a test. C-31 also runs a script acting as the engine that must be
refused a delete, an edit to its own role, and a grant outside the two-role
list.

## Every permission, traced to the engine call that needs it

Traced on 2026-09-27 from the v1.156.0 source tree and Google's own method-to-permission pages. Scope: the six kinds that
`platform-config/charts/system/templates/*.yaml` and `charts/claims/templates/databases.yaml` render, on the paths those
charts can reach: **create** (object new, cloud resource absent), **acquire** (object new, cloud resource with that name
exists), **steady state** (reconcile, nothing changed), **update** (a field change), **delete**.

Labels. **[C]**: the code was read at v1.156.0, *and* a Google page maps that API method to that permission.
**[I]**: inferred, with the reason given. Rows that make no Google call are routing steps; their [C] means the code was read.

Path shorthand. `kcc/` = `https://github.com/GoogleCloudPlatform/k8s-config-connector/tree/v1.156.0/`.
`tpg/` = `kcc/third_party/github.com/hashicorp/terraform-provider-google-beta/google-beta/`.
Permission sources (full URLs at the end): **S1** Cloud SQL "Required permissions for Cloud SQL Admin API methods";
**S2** IAM REST method page ("Authorization requires the following IAM permission"); **S3** IAM audit-logging page
(method → permission); **S4** Resource Manager v1 REST method page; **S5** Artifact Registry REST method page;
**S6** Artifact Registry audit-logging page (method → permission); **S7** Artifact Registry roles-and-permissions page.

Which controller handles each kind (no Google call; decides which code the rows below trace) [C]:

| Kind | Controller at v1.156.0 | Evidence |
|---|---|---|
| IAMServiceAccount | Terraform, `google_service_account` | `kcc/pkg/controller/resourceconfig/static_config.go:354` |
| IAMPolicyMember | IAMPolicyMember controller → Terraform IAM client | `static_config.go:353`; `kcc/pkg/controller/registration/registration_controller.go:registerDefaultController` |
| ArtifactRegistryRepository | Terraform (the default). Direct only with the `alpha.cnrm.cloud.google.com/reconciler: direct` annotation or a ConfigConnectorContext override; the charts set neither and cluster mode has no ConfigConnectorContext | `static_config.go:64`; `kcc/pkg/controller/parent/controller.go:determineControllerType`; `platform-config/config-connector/configconnector.yaml` |
| SQLInstance | direct | `static_config.go:509` |
| SQLDatabase | Terraform, `google_sql_database` | `static_config.go:508` |
| SQLUser | Terraform, `google_sql_user` | `static_config.go:511` |

No `X-Goog-User-Project` header is sent, so no `serviceusage.services.use` is needed. The header is set only when user-project
override is on (`tpg/transport/transport.go:39`). `--user-project-override` defaults to false
(`kcc/cmd/manager/main.go:77`), and the in-tree cluster-mode manifest passes only `--prometheus-scrape-endpoint`
[C for the flag default; I for the 1.156.0 operator's own bundled manifest, since the tree at that tag carries only the
1.148.0 channel].

---

### IAMServiceAccount (not durable; the charts give it no abandon annotation)

| # | Lifecycle step | Code (file:function) | Google API method | Permission | Label |
|---|---|---|---|---|---|
| SA-1 | all — routing: import ID `projects/P/serviceAccounts/<name>@P.iam.gserviceaccount.com` from the service mapping's idTemplate and the `project-id` annotation; the importer only parses | `kcc/pkg/krmtotf/fetchlivestate.go:fetchLiveStateFromID` → `tpg/services/resourcemanager/resource_google_service_account.go:resourceGoogleServiceAccountImport` | no Google call | — | [C] |
| SA-2 | create, acquire, steady state — read (404 → "absent") | `fetchlivestate.go:fetchLiveStateFromID` → `resource_google_service_account.go:resourceGoogleServiceAccountRead` | iam v1 `projects.serviceAccounts.get` | `iam.serviceAccounts.get` | [C] S2 |
| SA-3 | create | `resourceGoogleServiceAccountCreate` | `projects.serviceAccounts.create` | `iam.serviceAccounts.create` | [C] S2 |
| SA-4 | create — waits out IAM's eventual consistency, then reads (≥3 calls) | `resourceGoogleServiceAccountCreate` (`transport_tpg.Retry`, `PollingWaitTime` → `resourceServiceAccountPollRead`) → `resourceGoogleServiceAccountRead` | `projects.serviceAccounts.get` | `iam.serviceAccounts.get` | [C] S2 |
| SA-5 | acquire, steady state — no diff, no write. Fields no server-side-apply manager owns (e.g. `disabled`) are filled from the live value | `kcc/pkg/controller/tf/controller.go:sync` (`diff.Empty()` l.389); `kcc/pkg/krmtotf/managedfields.go:resolveUnmanagedFields` | no Google call | — | [C] |
| SA-6 | update (displayName, description) — fetch etag | `resourceGoogleServiceAccountUpdate` | `projects.serviceAccounts.get` | `iam.serviceAccounts.get` | [C] S2 |
| SA-7 | update — write | `resourceGoogleServiceAccountUpdate` | `projects.serviceAccounts.patch`, updateMask `description,display_name` | `iam.serviceAccounts.update` | [C] S3 (PatchServiceAccount; the patch REST page states no permission) |
| SA-8 | delete — routing: no abandon annotation; no parent reference, so not orphaned | `tf/controller.go:sync` (l.299, `isOrphaned`) | no Google call | — | [C] |
| SA-9 | delete — read | `fetchlivestate.go:FetchLiveStateForDelete` → `resourceGoogleServiceAccountRead` | `projects.serviceAccounts.get` | `iam.serviceAccounts.get` | [C] S2 |
| SA-10 | delete — delete | `tf/controller.go:sync` → `TFResource.Apply(Destroy)` → `resourceGoogleServiceAccountDelete` | `projects.serviceAccounts.delete` | `iam.serviceAccounts.delete` | [C] S2 |

### IAMPolicyMember (not durable; real delete; three targets)

| # | Lifecycle step | Code (file:function) | Google API method | Permission | Label |
|---|---|---|---|---|---|
| PM-1 | all — routing: none of the three target kinds is IAM-direct (`IsIAMDirect` lists only PrivateCACAPool, GKEHubScope) or DCL-based (IAMServiceAccount and Project are `Releasable: false`; AR is absent) → Terraform IAM client | `kcc/pkg/controller/iam/iamclient/iamclient.go:Get/Set/DeletePolicyMember`; `kcc/pkg/controller/direct/registry/registry.go:IsIAMDirect`; `kcc/pkg/dcl/metadata/gvk2stv.go:IsDCLBasedResourceKind` | no Google call | — | [C] |
| PM-2 | all — routing: `resourceRef` kind IAMServiceAccount or ArtifactRegistryRepository, by name → Kubernetes read of that object, which must be Ready; its import ID becomes the policy's target | `tfiamclient.go:getResourceConfigForReferencedResource`, `getResource` (Ready check l.619), `getResourceID` | no Google call | — | [C] |
| PM-3 | all — routing: `resourceRef` kind Project, external `projects/<id>` → service-mapping lookup; the `google_project` importer only parses | `tfiamclient.go:getResourceConfigForExternalRef`, `buildUnstructuredIAMSkeletonFromReference` (l.505); `tpg/services/resourcemanager/resource_google_project.go:resourceProjectImportState` | no Google call | — | [C] |
| PM-4 | all — routing: `member` is a literal string; the IAM member resource is built with `SkipImport: true`, so its importer (which would read the policy) never runs | `helpers.go:ResolveMemberIdentity`; `tfiamclient.go:newServiceMappingForGVKAndTFResourceName` (l.822) | no Google call | — | [C] |
| PM-5 | create, acquire, steady state — **Project**: read policy, twice per reconcile (once in GetPolicyMember, once in SetPolicyMember) | `kcc/pkg/controller/iam/policymember/iampolicymember_controller.go:doReconcile` (l.290, l.304) → `tfiamclient.go:GetPolicyMember`, `SetPolicyMember` → `FetchLiveState` → `tpg/tpgiamresource/resource_iam_member.go:resourceIamMemberRead` → `tpg/tpgiamresource/iam.go:iamPolicyReadWithRetry` → `tpg/services/resourcemanager/iam_project.go:GetResourceIamPolicy` | cloudresourcemanager v1 `projects.getIamPolicy`, requestedPolicyVersion 3 | `resourcemanager.projects.getIamPolicy` | [C] S4 |
| PM-6 | same — **IAMServiceAccount** target | … → `tpg/services/resourcemanager/iam_service_account.go:GetResourceIamPolicy` | iam v1 `projects.serviceAccounts.getIamPolicy`, requestedPolicyVersion 3 | `iam.serviceAccounts.getIamPolicy` | [C] S3 |
| PM-7 | same — **ArtifactRegistryRepository** target | … → `tpg/services/artifactregistry/iam_artifact_registry_repository.go:GetResourceIamPolicy` | artifactregistry v1 `projects.locations.repositories.getIamPolicy` (GET, no version) | `artifactregistry.repositories.getIamPolicy` | [C] S6 |
| PM-8 | acquire, steady state — binding present → empty diff → no write | `tfiamclient.go:SetPolicyMember` (`diff.Empty()`) | no Google call | — | [C] |
| PM-9 | create — **Project**: read-modify-write, then ≥3 verification reads and one final read | `SetPolicyMember` → `Apply` → `resource_iam_member.go:resourceIamMemberCreate` → `tpg/tpgiamresource/iam_batching.go:BatchRequestModifyIamPolicy` (project members are registered with batching, `tpg/provider/provider.go:1930`) → `iam.go:iamPolicyReadModifyWrite` → `iam_project.go:SetResourceIamPolicy` | `projects.getIamPolicy`, `projects.setIamPolicy` with updateMask `bindings,etag,auditConfigs` | `resourcemanager.projects.getIamPolicy`, `resourcemanager.projects.setIamPolicy` (the conditioned grant) | [C] S4 |
| PM-10 | create — **IAMServiceAccount** | … → `iamPolicyReadModifyWrite` → `iam_service_account.go:SetResourceIamPolicy` | `serviceAccounts.getIamPolicy`, `serviceAccounts.setIamPolicy` (no updateMask) | `iam.serviceAccounts.getIamPolicy`, `iam.serviceAccounts.setIamPolicy` | [C] S3 |
| PM-11 | create — **ArtifactRegistryRepository** | … → `iamPolicyReadModifyWrite` → `iam_artifact_registry_repository.go:SetResourceIamPolicy` | `repositories.getIamPolicy`, `repositories.setIamPolicy` (POST body `{"policy":…}`, no updateMask) | `artifactregistry.repositories.getIamPolicy`, `artifactregistry.repositories.setIamPolicy` | [C] S6 |
| PM-12 | update — any spec edit is refused at admission: "the IAMPolicyMember's spec is immutable" | `kcc/pkg/webhook/immutable_fields_validator.go:handleIAMPolicyMember` | no Google call | — | [C] |
| PM-13 | delete — routing: no abandon annotation on these objects in the charts. If the referenced IAMServiceAccount or ArtifactRegistryRepository object is already gone, `ReferenceNotFound` releases the finalizer with no Google call; if it exists but is not Ready, requeue | `iampolicymember_controller.go:doReconcile` (l.272–274) | no Google call | — | [C] |
| PM-14 | delete — read (binding absent → `ErrNotFound` → done), then read-modify-write removing the member, ≥3 verification reads, final read | `tfiamclient.go:DeletePolicyMember` → `FetchLiveState` → `Apply(Destroy)` → `resource_iam_member.go:resourceIamMemberDelete` → `iamPolicyReadModifyWrite` | the target's `getIamPolicy` + `setIamPolicy`, as PM-9/10/11 | the target's `getIamPolicy` + `setIamPolicy` | [C] S3, S4, S6 |

### ArtifactRegistryRepository (durable, abandon)

| # | Lifecycle step | Code (file:function) | Google API method | Permission | Label |
|---|---|---|---|---|---|
| AR-1 | all — routing: import ID `projects/P/locations/L/repositories/<name>`; the importer only parses | `fetchlivestate.go:fetchLiveStateFromID` → `tpg/services/artifactregistry/resource_artifact_registry_repository.go:resourceArtifactRegistryRepositoryImport` | no Google call | — | [C] |
| AR-2 | create, acquire, steady state — read (404 → absent) | `resourceArtifactRegistryRepositoryRead` | `projects.locations.repositories.get` | `artifactregistry.repositories.get` | [C] S5 |
| AR-3 | create | `resourceArtifactRegistryRepositoryCreate` | `projects.locations.repositories.create` (`?repository_id=`) | `artifactregistry.repositories.create` | [C] S5 |
| AR-4 | create — poll the long-running operation (skipped if the first response is already done) | `artifact_registry_operation.go:ArtifactRegistryOperationWaitTimeWithResponse` → `tpg/tpgresource/common_operation.go:OperationWait` → `ArtifactRegistryOperationWaiter.QueryOp` | `projects.locations.operations.get` | **not documented** | [I] — neither the REST page nor the audit-logging page names one, and Google's AR permission list has no `artifactregistry.operations.*` permission; which permission, if any, is checked is unknown until the desk rehearsal |
| AR-5 | create — final read | `resourceArtifactRegistryRepositoryRead` | `repositories.get` | `artifactregistry.repositories.get` | [C] S5 |
| AR-6 | acquire, steady state — no diff, no write | `tf/controller.go:sync` | no Google call | — | [C] |
| AR-7 | update (description, labels); also on acquire if the live labels or description differ | `resourceArtifactRegistryRepositoryUpdate` | `repositories.patch`, `?updateMask=` changed fields | `artifactregistry.repositories.update` | [C] S6 (UpdateRepository; the patch REST page states no permission) |
| AR-8 | update — read after write | `resourceArtifactRegistryRepositoryRead` | `repositories.get` | `artifactregistry.repositories.get` | [C] S5 |
| AR-9 | delete — abandon annotation checked before any live-state fetch | `tf/controller.go:sync` l.299 (before `FetchLiveStateForDelete` l.324) | **no Google call** | — | [C] |

### SQLInstance (durable, abandon; direct controller)

| # | Lifecycle step | Code (file:function) | Google API method | Permission | Label |
|---|---|---|---|---|---|
| SI-1 | all paths, **delete included** — routing: read the `<claim>-admin` Secret for `rootPassword` | `kcc/pkg/controller/direct/sql/sqlinstance_controller.go:AdapterForObject` → `sqlinstance_resolverefs.go:resolveRootPasswordRef` | no Google call (Kubernetes Secret read) | — | [C] |
| SI-2 | all — routing: `privateNetworkRef.external` is only trimmed; no lookup | `sqlinstance_resolverefs.go:resolvePrivateNetworkRef` → `kcc/apis/compute/refs/computenetwork_reference.go:Normalize` → `kcc/apis/refs/v1beta1/helper.go:NormalizeWithFallback` | no Google call | — | [C] |
| SI-3 | all paths, **delete included** — Find. Any error at all is read as "not found" | `sqlinstance_controller.go:Find` (l.601–603), called from `kcc/pkg/controller/direct/directbase/directbase_controller.go:doReconcile` l.357 | sqladmin v1beta4 `instances.get` | `cloudsql.instances.get` | [C] S1 |
| SI-4 | create | `sqlinstance_controller.go:insertInstance` | `instances.insert` | `cloudsql.instances.create` | [C] S1 |
| SI-5 | create — poll | `sqlinstance_controller.go:pollForLROCompletion` | `operations.get` | `cloudsql.instances.get` | [C] S1 |
| SI-6 | create — re-read | `insertInstance` | `instances.get` | `cloudsql.instances.get` | [C] S1 |
| SI-7 | create — list users (looking for a MySQL `root` to delete; Postgres lists and deletes nothing) | `insertInstance` l.721 | `users.list` | `cloudsql.users.list` | [C] S1 |
| SI-8 | acquire, steady state — last-modified cookie matches, or `DiffInstances` finds nothing → no write | `sqlinstance_controller.go:Update` → `CompareLastModifiedCookie`, `DiffInstances` | no Google call | — | [C] |
| SI-9 | update or acquire — `databaseVersion` or `edition` differs | `Update` | `instances.patch` (+ SI-11) | `cloudsql.instances.get`, `cloudsql.instances.update` | [C] S1 |
| SI-10 | update or acquire — any other field differs (e.g. `activationPolicy` back to ALWAYS after a park). The PUT body carries `rootPassword` (`sqlinstance_mappings.go:54`) | `Update` l.908 | `instances.update` (PUT) | `cloudsql.instances.update` | [C] S1 |
| SI-11 | update — poll, then re-read | `Update` → `pollForLROCompletion`; `instances.get` | `operations.get`, `instances.get` | `cloudsql.instances.get` | [C] S1 |
| SI-12 | delete — abandon checked **after** SI-1 and SI-3 have run; no delete call | `directbase_controller.go:doReconcile` l.392 | no delete call (SI-3's `instances.get` is the one Google call) | — | [C] |

### SQLDatabase (durable, abandon + `deletionPolicy: ABANDON`)

| # | Lifecycle step | Code (file:function) | Google API method | Permission | Label |
|---|---|---|---|---|---|
| DB-1 | all — routing: `instanceRef` by name → SQLInstance object must exist and be Ready | `kcc/pkg/krmtotf/references.go:ResolveReferenceObject` (l.192) | no Google call | — | [C] |
| DB-2 | all — routing: import ID parsed; the importer also sets `deletion_policy = DELETE` | `tpg/services/sql/resource_sql_database.go:resourceSQLDatabaseImport` | no Google call | — | [C] |
| DB-3 | create, acquire, steady state — read (404, or a 400 "instance is not running" rewritten to 404 → absent) | `resourceSQLDatabaseRead`; `tpg/services/sql/sql_utils.go:transformSQLDatabaseReadError` | `databases.get` | `cloudsql.databases.get` | [C] S1 |
| DB-4 | create | `resourceSQLDatabaseCreate` | `databases.insert` | `cloudsql.databases.create` | [C] S1 |
| DB-5 | create, update — poll | `tpg/services/sql/sqladmin_operation.go:SqlAdminOperationWaitTime` → `QueryOp` | `operations.get` | `cloudsql.instances.get` | [C] S1 |
| DB-6 | create, update — read after write | `resourceSQLDatabaseRead` | `databases.get` | `cloudsql.databases.get` | [C] S1 |
| DB-7 | **acquire** — on the first reconcile the live `deletion_policy` reads DELETE (importer default, no mutable-but-unreadable annotation yet) against spec ABANDON → diff → update | `fetchlivestate.go:withMutableButUnreadableFields` (nothing to preset) → `resourceSQLDatabaseUpdate` | `databases.update` (PUT; body charset, collation, name, instance) | `cloudsql.databases.update` | [C] S1 (code-derived; not yet seen in a run) |
| DB-8 | steady state — the annotation now presets ABANDON → no diff | `tf/controller.go:sync` | no Google call | — | [C] |
| DB-9 | update — none reachable from the chart: it renders only constants (`resourceID: app`, an instanceRef fixed by the object's own name, `deletionPolicy: ABANDON`) | `charts/claims/templates/databases.yaml` | no Google call | — | [C] |
| DB-10 | delete — abandon annotation checked before any fetch (and `resourceSQLDatabaseDelete` would also return early on ABANDON, l.383) | `tf/controller.go:sync` l.299 | **no Google call** | — | [C] |

### SQLUser (durable, abandon; type CLOUD_IAM_SERVICE_ACCOUNT)

| # | Lifecycle step | Code (file:function) | Google API method | Permission | Label |
|---|---|---|---|---|---|
| SU-1 | all — routing: `instanceRef` by name → SQLInstance object Ready | `references.go:ResolveReferenceObject` | no Google call | — | [C] |
| SU-2 | all — routing: import ID `P/<instance>/<name>` parsed | `tpg/services/sql/resource_sql_user.go:resourceSqlUserImporter` | no Google call | — | [C] |
| SU-3 | create, acquire, steady state — read (1 of 2). **404 and 403 both mean "gone"** | `resourceSqlUserRead` l.330–340 → `handleUserNotFoundError` l.32–43 | `users.list` | `cloudsql.users.list` | [C] S1 |
| SU-4 | create, acquire, steady state — read (2 of 2), to tell Postgres from MySQL | `resourceSqlUserRead` l.343 | `instances.get` | `cloudsql.instances.get` | [C] S1 |
| SU-5 | create (no `host`, so no pre-check `instances.get`) | `resourceSqlUserCreate` | `users.insert`, no password | `cloudsql.users.create` | [C] S1 |
| SU-6 | create — poll | `SqlAdminOperationWaitTime` | `operations.get` | `cloudsql.instances.get` | [C] S1 |
| SU-7 | create — read after write | `resourceSqlUserRead` | `users.list`, `instances.get` | `cloudsql.users.list`, `cloudsql.instances.get` | [C] S1 |
| SU-8 | acquire, steady state — no diff, no write | `tf/controller.go:sync` | no Google call | — | [C] |
| SU-9 | update — `name` and `type` are ForceNew; `resourceSqlUserUpdate` calls the API only if `password` or `password_policy` changed, and the chart sets neither | `resourceSqlUserUpdate` l.447 | no Google call | — | [C] |
| SU-10 | delete — abandon annotation checked before any fetch | `tf/controller.go:sync` l.299 | **no Google call** | — | [C] |

**Totals:** 65 rows: 38 make a Google call and 27 are routing steps. 64 are [C] and 1 is [I] (AR-4). ADR-0018, written from an earlier working trace, counted in different units ("45 steps, 34 calls"); this table is the one to use.

---

### 1. The 21 permissions cover every call on these paths

**Yes, with one unknown.** Every call on the charts' paths maps to one of the 21 [C], except AR-4: Artifact Registry's
`operations.get` during a repository create. Google documents no permission for it, and no `artifactregistry.operations.*`
permission exists [I]. The desk rehearsal settles it.

All 21 are used by at least one reachable call, so none is surplus:

| Permission | Rows |
|---|---|
| cloudsql.instances.get | SI-3, SI-5, SI-6, SI-9, SI-11, DB-5, SU-4, SU-6, SU-7 |
| cloudsql.instances.create / .update | SI-4 / SI-9, SI-10 |
| cloudsql.databases.create / .get / .update | DB-4 / DB-3, DB-6 / **DB-7 only (acquire)** |
| cloudsql.users.create / .list | SU-5 / SI-7, SU-3, SU-7 |
| artifactregistry.repositories.create / .get / .update | AR-3 / AR-2, AR-5, AR-8 / AR-7 |
| artifactregistry.repositories.getIamPolicy / .setIamPolicy | PM-7, PM-14 / PM-11, PM-14 |
| iam.serviceAccounts.create / .get / .update / .delete | SA-3 / SA-2, SA-4, SA-6, SA-9 / SA-7 / SA-10 |
| iam.serviceAccounts.getIamPolicy / .setIamPolicy | PM-6, PM-14 / PM-10, PM-14 |
| resourcemanager.projects.getIamPolicy / .setIamPolicy | PM-5, PM-14 / PM-9, PM-14 |

`roles/compute.viewer` is used by **no traced call**. No code on these paths calls a Compute API. S1 lists only
`cloudsql.instances.create` for `instances.insert`. The `compute.*` entries on that page belong to the *console* task
"Create an instance", not to the API method. Whether Cloud SQL checks a compute permission on the caller for a
private-network instance is still unstated [I, unverified], as ADR-0018 §2 says.

### 2. Every other call needs a permission the roles leave out (nine, not the four ADR-0018 counted)

The substance holds: every call the charts do not reach needs a permission the roles leave out. **But there are nine such
call sites, needing eight permissions, not four.** Each is [C]: code read, permission from S1, S2, S3 or S6.

| Call | Where (v1.156.0) | Permission | Why the charts never reach it | In the ADR's four? |
|---|---|---|---|---|
| user delete, MySQL `root` | `sqlinstance_controller.go:insertInstance` l.728–731 | `cloudsql.users.delete` | runs only if `DatabaseVersion` starts with `MYSQL`; the claims schema allows only `POSTGRES_16`, `POSTGRES_17` | yes |
| user password update | `resource_sql_user.go:resourceSqlUserUpdate` l.468 | `cloudsql.users.update` | only when `password` or `password_policy` changes; the chart's one user is an IAM user with neither | yes |
| repository delete | `resource_artifact_registry_repository.go:resourceArtifactRegistryRepositoryDelete` | `artifactregistry.repositories.delete` | the abandon annotation returns first (`tf/controller.go:299`) | yes |
| service-account enable / disable | `resource_google_service_account.go:resourceGoogleServiceAccountUpdate` l.243 / l.254 | `iam.serviceAccounts.enable` / `.disable` | only when `disabled` changes. The charts never set `spec.disabled`, and `resolveUnmanagedFields` fills it from the live value, so even an out-of-band disable creates no diff | yes |
| **instance delete** | `sqlinstance_controller.go:Delete` l.977 | `cloudsql.instances.delete` | abandon is checked first (`directbase_controller.go:392`) | **no** |
| **database delete** | `resource_sql_database.go:resourceSQLDatabaseDelete` | `cloudsql.databases.delete` | abandon annotation (`tf/controller.go:299`); the TF `deletion_policy: ABANDON` would also return early | **no** |
| **SQLUser's own delete** | `resource_sql_user.go:resourceSqlUserDelete` l.523 | `cloudsql.users.delete` | abandon annotation only. `sql.yaml` lists `deletion_policy` as an ignored field for SQLUser, so the annotation is the only in-code guard | **no** |
| **instance clone** | `sqlinstance_controller.go:cloneInstance` l.673 | `cloudsql.instances.clone` | only when `spec.cloneSource` is set; the chart never sets it | **no** |

ADR-0018 named the first four only. The three deletes it missed are the very calls the third lock exists to refuse.

A related fact for the password line: the `postgres` password cannot be reset through `users.update`, but every
`instances.update` PUT carries `rootPassword` (`sqlinstance_mappings.go:54`). `sqlinstance_equality.go` ignores
`rootPassword` when it compares, so the password alone never triggers an update [C]. Whether Cloud SQL applies it when
another field does trigger one is ADR-0017 §8's open question [I]. That call needs only `cloudsql.instances.update`.

### 3. How an IAM member reads and writes each policy

Common to all three targets [C]:
- **Every reconcile reads the policy twice** (`GetPolicyMember`, then `SetPolicyMember`) and writes nothing when the binding is already there (PM-5 to PM-8).
- A write is a read-modify-write (`tpgiamresource/iam.go:iamPolicyReadModifyWrite`): read with the etag, add or subtract one binding, set the whole policy, then re-read until 3 reads confirm the change (backoff capped at 30 s), then one final read.
- On an etag conflict (409) it starts over with backoff, up to 30 s. A per-resource mutex serialises writes inside the controller.
- An existing member is matched case-insensitively, ignoring a `deleted:` prefix (`iamMemberMatches`).

| Target | Read | Write | Mask and version |
|---|---|---|---|
| Project (`external: projects/<id>`) | cloudresourcemanager **v1** `projects.getIamPolicy`, `options.requestedPolicyVersion: 3` (`iam_project.go:GetResourceIamPolicy`) | `projects.setIamPolicy` (`iam_project.go:SetResourceIamPolicy`) | **`updateMask: "bindings,etag,auditConfigs"`**, hard-coded at `iam_project.go:79–80`. The create modifier also sets `policy.version = 3` (`resource_iam_member.go:218`). Google's default mask when none is sent is `"bindings, etag"` (S4). Registered with batching, so concurrent project members can be merged into one write; same calls either way [I whether KCC's provider has the batcher on]. |
| IAMServiceAccount (by name) | iam v1 `projects.serviceAccounts.getIamPolicy`, `options.requestedPolicyVersion=3` | `projects.serviceAccounts.setIamPolicy`, body `{policy}` | no updateMask set |
| ArtifactRegistryRepository (by name; the reference field is the repository's short name, `parseNameFromID`) | `GET …/repositories/R:getIamPolicy`, no version parameter | `POST …/repositories/R:setIamPolicy`, body `{"policy": …}` | no updateMask set |

Two consequences:
- The project write carries `auditConfigs` in its mask. It sends back whatever audit configs it read, unchanged. Whether a `setIamPolicy` whose mask names `auditConfigs` passes the `modifiedGrantsByRole` condition is still unverified, as ADR-0018 says; `gcloud` does not send this mask, so the rehearsal cannot show it.
- On delete, a member whose referenced Config Connector object is already gone makes **no Google call** (PM-13). The team's `roles/artifactregistry.writer` binding is therefore left on the abandoned repository whenever the repository object is deleted before its IAMPolicyMember [C code; the ordering is I]. The workload-identity binding goes with the service account itself. Project-level members use an external reference, so they are always removed.

### 4. Failures that do not look like a missing permission

**Missing `cloudsql.instances.get`.** `sqlinstance_controller.go:596–612`:

```go
instance, err := a.sqlInstancesClient.Get(a.projectID, a.resourceID).Context(ctx).Do()
if err != nil {
    return false, nil
}
```

A 403 becomes "does not exist", so the controller goes on to create [C].
- **Instance already exists** (steady state, acquire, every rebuild): `instances.insert` is refused. The status is `Ready=False`, reason `UpdateFailed`, message `Update call failed: error creating: creating SQLInstance <name> failed: googleapi: Error 409: …`. The wrapper chain is [C] (`directbase_controller.go:443`, `sqlinstance_controller.go:710`, `lifecyclehandler/handler.go:312`); Google's exact 409 text is [I]. It reads exactly like a failed adoption.
- **Fresh create:** the insert succeeds in Google, then `pollForLROCompletion` fails on `operations.get` (which also needs `instances.get`). `sqlinstance_controller.go:1037–1039` assigns the nil result to `op` and then reads `op.Name`. That is a **nil-pointer panic**, recovered as an internal error `observed a panic: runtime error: invalid memory address or nil pointer dereference` (`kcc/pkg/execution/builtin.go:RecoverWithInternalError`). It shows in the controller log; no status condition is written. The next reconcile hits the 409 case above. The code is [C], including that `OperationsGetCall.Do` returns nil on error at google-api-go-client v0.287.1. The runtime text is standard Go.
- **Knock-on effects:** SQLUser's read fails outright (`resource_sql_user.go:343` returns the error): `Update call failed: error fetching live state: error reading underlying resource: summary: googleapi: Error 403…`. SQLDatabase and SQLUser operation waits fail too. Both also stay waiting on a SQLInstance that never turns Ready (DB-1, SU-1).

**Missing `cloudsql.users.list`:**
- **SQLInstance, fresh create only** (`sqlinstance_controller.go:721–723`): the instance gets created, then `Update call failed: error creating: listing SQLInstance <name> users failed: googleapi: Error 403…`. It heals itself on the next reconcile: Find now succeeds and the update path never lists users [C].
- **SQLUser, every reconcile** (`resource_sql_user.go:32–43` and `330–340`): `handleUserNotFoundError` treats **403 like 404**. It logs `[WARN] Removing SQL User "<name>" in instance "<inst>" because it's gone` and clears the ID, so Config Connector decides to *create* the user. If the user exists, the insert fails as `Update call failed: error applying desired state: summary: Error, failed to insert user <name> into instance <inst>: …` (or `Error, failure waiting for insertion of …` if the failure surfaces in the operation). The wrapper is [C]; which of the two appears, and Google's text, are [I]. Either way it reads as "user already exists", not as a missing permission. A 403 permission error is not retried (`tpg/transport/error_retry_predicates.go`: only quota-flavoured 403s are).

Same family, for the runbook:
- A missing `iam.serviceAccounts.get` makes a service-account create succeed and then retry for up to 5 minutes. The create path deliberately retries a 403 `Permission 'iam.serviceAccounts.get' denied on resource (or it may not exist)`. It ends in `Error reading service account after creation` [C].
- While an instance is stopped, `databases.get`'s 400 "instance is not running" is rewritten to 404 (DB-3), so the SQLDatabase looks absent and a create is attempted [C].

**One delete-path finding:** SI-1 runs even on an abandon delete. If the `<claim>-admin` Secret is already gone, as it can be when a whole namespace is deleted, `resolveRootPasswordRef` returns `SecretNotFound`. `doReconcile` (l.346–351) treats that as an unresolvable dependency and requeues without end, so the SQLInstance keeps its finalizer and the namespace can hang in Terminating. This is [C] for the code and [I] until the return build shows the ordering. The platform-config chart README's offboarding steps delete the System's claims Application first, while the Secret still exists, for this reason.

---

### Sources read

Local:
- `/Users/ronakpatel/code/platform-factory/platform-factory/docs/adr/0018-engine-permissions-are-a-list-in-platform-roles.md`
- `/Users/ronakpatel/code/platform-factory/platform-config/charts/system/templates/{identity,access,registry,namespace,argocd,configmap,guard,_helpers}.yaml|tpl`
- `/Users/ronakpatel/code/platform-factory/platform-config/charts/claims/templates/{databases.yaml,_helpers.tpl}`, `charts/claims/values.schema.json`
- `/Users/ronakpatel/code/platform-factory/platform-config/config-connector/configconnector.yaml`, `environments/reference.yaml`

Config Connector v1.156.0 (raw files under `https://raw.githubusercontent.com/GoogleCloudPlatform/k8s-config-connector/v1.156.0/`):
- `pkg/controller/resourceconfig/static_config.go`, `selector.go`; `pkg/controller/registration/registration_controller.go`; `pkg/controller/parent/controller.go`
- `pkg/controller/tf/controller.go`; `pkg/krmtotf/{fetchlivestate,krmtotf,managedfields,references,errors}.go`
- `pkg/controller/direct/directbase/directbase_controller.go`; `pkg/controller/direct/registry/registry.go`
- `pkg/controller/direct/sql/{sqlinstance_controller,client,sqlinstance_resolverefs}.go`; `sqlinstance_mappings.go`, `sqlinstance_equality.go` (searched)
- `pkg/controller/iam/policymember/iampolicymember_controller.go`; `pkg/controller/iam/iamclient/{iamclient,tfiamclient,helpers}.go`
- `pkg/controller/lifecyclehandler/handler.go`; `pkg/execution/builtin.go`; `pkg/webhook/immutable_fields_validator.go`
- `pkg/dcl/metadata/{metadata,gvk2stv}.go`; `pkg/gvks/externalonlygvks/externalonlygvks.go`
- `apis/compute/refs/computenetwork_reference.go`; `apis/refs/v1beta1/helper.go`; `apis/sql/v1beta1/sqlinstance_types.go` (searched)
- `pkg/config/controllerconfig.go`; `pkg/tf/provider/provider.go`; `cmd/manager/main.go`; `go.mod`
- `operator/channels/packages/configconnector/1.148.0/cluster/workload-identity/0-cnrm-system.yaml`
- `config/servicemappings/{iam,resourcemanager,artifactregistry,sql}.yaml`
- `third_party/…/google-beta/provider/provider.go`
- `third_party/…/google-beta/services/resourcemanager/{resource_google_service_account,iam_service_account,iam_project,resource_google_project}.go`
- `third_party/…/google-beta/services/artifactregistry/{resource_artifact_registry_repository,iam_artifact_registry_repository,artifact_registry_operation}.go`
- `third_party/…/google-beta/services/sql/{resource_sql_database,resource_sql_user,sql_utils,sqladmin_operation}.go`
- `third_party/…/google-beta/tpgiamresource/{iam,resource_iam_member,iam_batching}.go`; `tpgresource/common_operation.go`; `transport/{error_retry_predicates,transport}.go`
- `https://raw.githubusercontent.com/googleapis/google-api-go-client/v0.287.1/sqladmin/v1beta4/sqladmin-gen.go` (`OperationsGetCall.Do`)

Google (method → permission):
- S1: https://cloud.google.com/sql/docs/postgres/iam-permissions#api-methods
- S2: https://cloud.google.com/iam/docs/reference/rest/v1/projects.serviceAccounts/create, …/get, …/delete, …/enable, …/disable (explicit). …/patch, …/getIamPolicy, …/setIamPolicy were read and state no permission.
- S3: https://cloud.google.com/iam/docs/audit-logging
- S4: https://docs.cloud.google.com/resource-manager/reference/rest/v1/projects/getIamPolicy, https://docs.cloud.google.com/resource-manager/reference/rest/v1/projects/setIamPolicy
- S5: https://docs.cloud.google.com/artifact-registry/docs/reference/rest/v1/projects.locations.repositories/create, …/get, …/delete (explicit). …/patch, …/getIamPolicy, …/setIamPolicy, and `projects.locations.operations/get` were read and state no permission.
- S6: https://docs.cloud.google.com/artifact-registry/docs/audit-logging
- S7: https://docs.cloud.google.com/iam/docs/roles-permissions/artifactregistry
- Also read: https://cloud.google.com/iam/docs/manage-access-service-accounts; https://docs.cloud.google.com/sql/docs/postgres/configure-private-ip (its compute permissions are for building the private-services-access connection, not for creating an instance)

## When Config Connector is upgraded

Nothing raises an alarm when a new Config Connector version calls an API
method these roles do not allow. The first person to notice is the operator,
in a paid session, as a failed resource. So on every Config Connector bump:
re-trace the table above against the new version's source, and re-run the desk
rehearsal. C-31 records how many fixes M2b needed, so the next reader knows the
going rate.

## Role lifecycle traps

- A deleted role turns every engine call into a 403 while its grants still
  show in the policy. `deletion_policy = "PREVENT"` is meant to make removing a
  role file fail the apply instead; that it does is not yet run.
- A rename is a delete plus a create, and costs the old ID for up to 44 days.
- A role's title and description are limited in **bytes**, not characters, and
  fail only at apply. The check counts bytes.
- The condition's text is part of the grant's identity: editing it is a destroy
  and a create. It is copied byte for byte from platform-bootstrap's layer 0,
  and the check compares the two.

## This repo's check

`.github/workflows/check.yml` runs `scripts/check_roles.py` with no cloud
credential. It refuses a role file that adds a durable delete, anything under
`iam.roles.`, a service-account key or an act-as verb, or that puts
`resourcemanager.projects.setIamPolicy` anywhere but the project-IAM file; and
a grant of a policy-setting role without the condition, or with a condition
that differs from layer 0's. It first proves it can refuse, on the fixtures in
`tests/must-fail/`, then checks the real files, then runs `terraform fmt` and
`validate`. Advisory for now, like every check in the org (ADR-0018 §7).

## Who approves changes here

Security, for the grants as well as the definitions:
[`.github/CODEOWNERS`](.github/CODEOWNERS) names `@platform-factory/security`.
That is declared, not enforced: no repo in this org requires a code owner's
approval yet, because one person is the only member of both teams and GitHub
does not let an author approve their own pull request (ADR-0018 §7). Security
approves the merge; the platform operator runs the apply, from a checkout
nothing forces to equal `main`.

## Part of the Platform Factory

One of the eight repos of the **Platform Factory** reference implementation.
The design seed — pattern docs, ADRs, the claims register — lives at
https://github.com/platform-factory/platform-factory-concept.

Platform Factory was designed and written by **Ronak Patel**
([thecloudgeek LLC](https://github.com/thecloudgeek)). Licensed Apache-2.0 —
the attribution to keep is in [NOTICE](NOTICE), and
[CITATION.cff](CITATION.cff) says how to cite it.
