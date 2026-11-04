# Jira Data Center Configuration Extractor — Product Specification

**Status:** Working draft for review  
**Last updated:** 2026-10-29

## 1. Purpose

Build a read-only CLI that extracts Jira Data Center field, screen, and relevant app configuration into a local, restartable data set. The data set should let migration analysts investigate custom fields—especially Elements Connect fields—and identify directly evidenced associations with Dynamic Forms, ScriptRunner, and other relevant app behavior.

The first release provides configuration evidence, not an automated Jira Cloud compatibility verdict.

## 2. Users and primary workflow

**Primary user:** A Jira administrator or migration analyst with permission to read Jira and any supported app configuration.

Typical workflow:

1. Configure the Jira DC base URL, user name, and password in the local, Git-ignored `.scratch/app-credentials.yaml`.
2. Start an extraction into a local output directory.
3. If interrupted, rerun against that directory; completed entity stages are retained and a failed stage can be retried.
4. Inspect the JSONL records and provenance with downstream analysis tools.

The CLI must never modify Jira or app configuration.

## 3. Goals

- Extract an analyzable inventory of Jira fields and screens and the configuration needed to understand their relationships.
- Capture directly observable Elements Connect, Dynamic Forms, ScriptRunner, and other supported app associations relevant to fields/screens.
- Make large extractions restartable without requiring a single monolithic JSON document.
- Make each record traceable to its source and extraction time.
- Never persist or log the Jira credentials used to run the export. Exported configuration is treated as metadata and is written as Jira returns it.
- Report incomplete or unavailable data sources rather than presenting a partial export as complete.

## 4. Non-goals for the initial release

- Declare a field or Jira instance “Cloud-migratable” or “not migratable.”
- Recommend replacements, redesigns, or remediation plans.
- Extract all Jira configuration (for example workflows, permissions, and project settings) unless later added as a separate scope. The one recorded exception is the project workflow scheme link in section 14.1; workflow definitions remain out of scope.
- Change Jira or app configuration.
- Guarantee extraction of app data that is not exposed through an accessible, supported API.

## 5. Initial extraction scope

### Jira configuration

- Custom field definitions and identifying metadata.
- Field contexts and options, where exposed by the source API.
- Field configurations and their associations to relevant schemes.
- Screens, tabs, and field placements.
- Screen schemes and issue type screen schemes, including their relationships to screens and projects/issue types when available through the supported APIs.

### App configuration and associations

**Scope decision (2026-11-03):** the initial release does **not** extract app configuration. No supported read-only configuration API was identified for Elements Connect, Dynamic Forms, or ScriptRunner, so each is reported as a declared gap with a reason and the evidence that no supported read route exists (section 6). The documented vendor exports are administration-screen downloads, not read-only configuration REST APIs; see [plugin-api-references.md](plugin-api-references.md). The extractor must not guess an endpoint or infer app configuration from field names.

What the initial release does collect:

- The installed user-installed app inventory and version, when Jira's UPM REST module (`GET /rest/plugins/1.0/`) is accessible. This is app presence/version evidence, not app configuration. The UPM module is internal and undocumented, so the stage is marked `"api": "internal"`, and it is advisory: an unavailable or unauthorized inventory is a coverage gap, not a failed required stage.
- A field-to-app association when a custom field's `schema.custom` type key begins with the exact key of a detected installed app. The complete field and app responses are preserved under `raw`, so the evidence is auditable. This is direct identifier evidence, not an association to app configuration; it does not claim the app's datasource, scripts, or behavior are known.
- No Elements Connect, Dynamic Forms, or ScriptRunner configuration record is fabricated. Those sources remain declared gaps.

An association must be supported by extracted source evidence. The extractor must not infer an association solely from a field name or a heuristic match without labeling it as an inference. The only association in this release is an exact match between a field type key and an installed app key.

## 6. Sources and capability reporting

Use Jira REST APIs and supported app-specific APIs. Exact endpoints, required permissions, and supported Jira/app versions remain to be discovered and validated against the target environment.

At the beginning of a run, discover or validate available sources where feasible. Record, per source and entity type:

- whether it is supported and reachable;
- Jira/app name and version when available;
- required capability that is missing, if detectable;
- extraction status and errors.

If an app API is unavailable, unauthorized, or unsupported, the run must report that gap. Do not silently report the related app configuration as fully extracted.

Installed app versions are read from Jira's UPM REST module at `GET /rest/plugins/1.0/` when the account can use it. UPM is internal and undocumented, so it is not an app-configuration source and its coverage entry is marked `"api": "internal"`. If it is unavailable or unauthorized, no app version is recorded; the map stays empty rather than guessing.

## 7. Authentication and safety

- Initial authentication: HTTP Basic authentication with a Jira Data Center user name and password, read from the `jira-dc` entry of the local, Git-ignored `.scratch/app-credentials.yaml` (`url`, `user`, `password`). Do not accept the password in command arguments, and do not place it in output, logs, or exported files. Personal access tokens are not supported yet.
- Validate TLS certificates by default. Any insecure TLS override, if supported, must be explicit and prominently warned about.
- Treat exported configuration as non-secret metadata: it is written verbatim, with no redaction or property filtering. Keep the export credentials out of persisted records, errors, and logs. Because configuration is not redacted, store the output directory with the access controls you would give the Jira configuration itself.
- Use read-only HTTP methods/endpoints. Do not provide mutation commands in the initial release.

## 8. Output and resumability

### Format

Write a directory-based JSON Lines data set rather than a single JSON document or CSV. Each line is one normalized record. The output must be deterministic enough for downstream tooling and must include provenance such as source system, source entity identifier, source endpoint or source category, and extraction timestamp.

The layout (schema version 1):

```text
<output-dir>/
  manifest.json
  checkpoint.json
  fields.jsonl
  custom-fields.jsonl
  custom-field-options.jsonl
  app-inventory.jsonl
  associations.jsonl
  projects.jsonl
  issue-types.jsonl
  issue-type-schemes.jsonl
  project-workflow-schemes.jsonl
  project-issue-type-view-screens.jsonl
  screens.jsonl
  screen-tabs.jsonl
  screen-tab-fields.jsonl
```

The manifest describes the export schema version, Jira/app versions where known, run identity/timestamps, and source/entity coverage. The checkpoint records completed and failed entity-type stages and enough error context to resume safely, without storing credentials.

### Checkpoint behavior

- Divide extraction into stages by entity type/source (for example, Jira fields, screens, Elements Connect, Dynamic Forms, ScriptRunner).
- Mark a stage complete only after its records have been durably written.
- On restart, retain completed stages and retry the failed/incomplete stage. Re-read that stage from its beginning if needed; exact API-page continuation is not required for the initial version.
- Make retries safe: identify records by stable source IDs and upsert/deduplicate them rather than appending duplicate records.
- Write records and checkpoint state so a crash cannot mark unwritten records complete. The implementation should define and test its durability/atomicity approach.

### Normalization and provenance

Records use a versioned normalized schema with source identifiers and provenance. Each record holds a normalized view plus the complete source object, as returned by Jira, under `raw`; no properties are dropped or redacted. Preserve app-specific details in explicit extension fields; do not silently discard observed behavior.

## 9. CLI behavior

Exact command names/options are implementation design decisions, but the initial user-visible behavior must cover:

- starting an extraction with a Jira URL and output directory;
- resuming/retrying from an existing output directory;
- reporting progress by entity/source stage;
- displaying a final coverage summary, including skipped/failed sources and counts;
- returning a non-success exit status when required Jira extraction fails or when the result is materially incomplete, while still preserving successful output for resumption.

## 10. Functional requirements

1. The CLI can connect to a configured Jira DC site using HTTP Basic authentication without exposing the password in process output or artifacts.
2. It extracts the in-scope Jira field, screen, and relationship records available to the authenticated account.
3. It queries supported app APIs for in-scope app configuration and field/screen associations where a supported read source exists. In this release that is the UPM installed-app inventory and exact field-to-app type-key evidence; app configuration itself is reported as a gap.
4. It records direct association evidence and provenance; it does not label an association as a migration blocker without a separately defined rule and evidence. It does not invent an app-configuration association.
5. It writes records incrementally in JSONL and maintains a checkpoint that supports retrying an incomplete entity stage.
6. Re-running after interruption does not lose completed stages or create duplicate logical records.
7. It records source/version/capability coverage and surfaces unavailable or unauthorized integrations.
8. It keeps the export credentials out of artifacts, exceptions, and logs, and writes exported configuration without redaction or property filtering.
9. It is read-only with respect to Jira and apps.

## 11. Acceptance criteria for an initial implementation

- A fixture-driven test demonstrates extraction and normalized records for representative custom fields, contexts, screens, tabs, and placements.
- A fixture-driven test demonstrates an Elements Connect field linked by exact field-type-key evidence to a detected installed app record, preserving source endpoint, source ID, extraction time, and the complete raw field and app objects. No app configuration is fabricated; the configuration gap is reported with evidence.
- Installed app versions are recorded where UPM exposes them; unavailable or versionless apps remain absent and are not guessed.
- Dynamic Forms and ScriptRunner being unavailable or unauthorized is represented as an explicit coverage gap, not as “no configuration found.”
- A simulated interruption during one entity stage followed by rerun preserves completed stages, retries the interrupted stage, and produces no duplicate records.
- The export password is absent from JSONL, checkpoint/manifest data, and logs; a secret-like value returned by a fixture is preserved verbatim under `raw`.
- The summary distinguishes “not found” from “not queried/unsupported/unauthorized/failed.”
- The CLI performs no write requests to Jira or app endpoints.

## 12. Open questions to resolve before implementation

1. Which Jira DC versions must be supported first, and what version is available for development/testing?
2. Which versions/editions of Elements Connect, Dynamic Forms, and ScriptRunner are in scope? Are their APIs enabled and accessible to the intended account?
3. What exact Dynamic Forms and ScriptRunner behaviors should count as relevant associations (for example, field visibility/requiredness/validation behavior, scripted fields, or screen-related scripts)?
4. Should app-specific records preserve selected raw response fragments in addition to normalized extension fields, to avoid losing configuration that the schema does not yet understand?
5. What are the expected output scale and retention constraints (number of fields/screens, site size, and whether multiple sites/runs share an output directory)?
6. Should a partially successful run exit nonzero, or should that depend on a strictness option?
7. Is an analyst-facing report needed in the first release, or are JSONL records plus a coverage summary sufficient?

## 13. Current project constraints

The project is implemented as a uv workspace. `pyproject.toml` defines Python `>=3.14`, dependencies including `httpx`, `httpx-limiter`, `openapi-httpx-client`, `rich`, and `typer`, and dev tools including `pytest`, `ruff`, and `ty`. These are current repository facts, not decisions that the product must retain; dependency and runtime choices should be revisited during implementation planning.

## 14. Recorded scope decisions

These decisions are deliberate and bound the initial release. Recording them here keeps them from reading as silent scope creep.

### 14.1 Project workflow scheme link

Non-goals exclude extracting all Jira configuration, including workflows. The exporter nevertheless reads `GET /rest/api/2/project/{projectKey}/workflowscheme` once per project and records the scheme ID and name, the default workflow, and the issue-type-to-workflow names. The field and screen inventory is hard to use without knowing which workflow a project and issue type run; one read per project is cheap and read-only. The boundary is deliberate: workflow definitions, transitions, conditions, validators, post-functions, and permissions are not extracted.

### 14.2 Internal project-config API for view screens

The exporter reads `GET /rest/projectconfig/1/issuetype/{projectKey}/{issueTypeId}/fields` to record the view screen for each project and issue type. No documented Jira DC route exposes that association, and the route is read-only. The risk is bounded by marking the stage `"api": "internal"`, recording failures in coverage instead of fabricating records, and limiting the normalized view to the View screen. Create and edit screens remain in `raw` only, and field configurations, field contexts, and screen schemes remain declared gaps. The route is undocumented and may change between Jira versions.

### 14.3 Connection-check command

`doctor` is an additional read-only diagnostic command. URL, DNS, TCP, TLS, proxy, and credential failures are common and otherwise surface as an export failure; `doctor` reports which check failed, stops at the first failure, exits nonzero on `fail`, and never writes export files. `export` reuses the same checks as a preflight before it touches the output directory.

### 14.4 Terminal progress and request pacing

Section 9 requires progress by entity/source stage. The exporter also prints one dot per record written, spaces requests evenly at `REQUESTS_PER_MINUTE` (120, a source constant rather than a command option), and shows a 429 wait inline as `(wait:Ns)` while retrying up to three times. These choices make a long, paced run observable from the terminal without a log, TUI, or progress file. They do not promise configurable pacing, a persisted wait state, or per-record progress as a machine-readable artifact; the coverage summary and checkpoint remain the durable progress record. Preflight and `doctor` requests are outside the pacing.
