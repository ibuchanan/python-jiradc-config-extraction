# CLI reference

Console script: `jira-config-export` (entry point `cli.main:main`). Run it from the repository root with `uv run jira-config-export`. Built with Typer; `--help` is available on the program and on each command. Shell completion is not installed.

## Commands

### `doctor`

```text
jira-config-export doctor
```

Checks the Jira connection. Takes no options. Reads credentials as described in [Provide Jira credentials](../how-to/provide-credentials.md).

Checks run in this order. The first failure stops the sequence; later checks report `skip`.

| Check | Verifies |
| --- | --- |
| `config` | The credentials file loads. |
| `url` | Scheme, host, and port of `url`. |
| `dns` | The host resolves. |
| `tcp` | A connection opens. |
| `http` | TLS and routing, through unauthenticated `GET /rest/api/2/serverInfo`. |
| `auth` | `GET /rest/api/2/myself` succeeds with Basic authentication. |
| `proxy` | Warns when `HTTPS_PROXY`-style variables are set. |

Check statuses: `pass`, `warn`, `fail`, `skip`.

Exit status: `1` if any check is `fail`, otherwise `0`. Warnings alone do not fail.

### `export`

```text
jira-config-export export [OPTIONS] OUTPUT_DIR
```

Exports configuration to `OUTPUT_DIR`. Requests are read-only (`GET`).

| Argument or option | Description |
| --- | --- |
| `OUTPUT_DIR` | Required. Directory for output files. Rerunning with the same directory resumes. |
| `-v`, `--verbose` | Log debug details to stderr. Default off. |

Behaviour:

- A preflight runs the `doctor` checks for config, network, and credentials. On failure the run stops before `OUTPUT_DIR` is touched.
- Output shows the absolute output directory, the run state (fresh or resuming), and one dot per record written per stage.
- Requests are paced at `REQUESTS_PER_MINUTE`, set to 120 near the top of `packages/cli/src/cli/main.py`.
- On HTTP 429 the wait appears as `(wait:30s)` and the request is retried up to 3 times.
- Completed stages are skipped on rerun. Failed or interrupted stages resume from their last committed unit.

Exit status: `0` on success; `1` if preflight fails, a queried stage fails, is unauthorized, or is unsupported, or an I/O error occurs. Declared gaps, which are `unsupported` without being queried, do not alone cause a nonzero status.

## Output files

All files are written to `OUTPUT_DIR`. Each line of a `.jsonl` file is one record.

| File | Stage | Source |
| --- | --- | --- |
| `fields.jsonl` | fields | `GET /rest/api/2/field` (custom fields only) |
| `custom-fields.jsonl` | field applicability | `GET /rest/api/2/customFields` |
| `custom-field-options.jsonl` | options | `GET /rest/api/2/customFields/{id}/options` |
| `projects.jsonl` | projects | `GET /rest/api/2/project?includeArchived=true` |
| `issue-types.jsonl` | issue types | `GET /rest/api/2/issuetype` |
| `issue-type-schemes.jsonl` | issue type schemes | `GET /rest/api/2/issuetypescheme/{id}` and `/associations` |
| `project-workflow-schemes.jsonl` | workflow schemes | `GET /rest/api/2/project/{key}/workflowscheme` |
| `project-issue-type-view-screens.jsonl` | view screens | `GET /rest/projectconfig/1/issuetype/{key}/{id}/fields` (internal) |
| `screens.jsonl` | screens | `GET /rest/api/2/screens` |
| `screen-tabs.jsonl` | tabs | `GET /rest/api/2/screens/{id}/tabs` |
| `screen-tab-fields.jsonl` | tab fields | `GET /rest/api/2/screens/{id}/tabs/{id}/fields` |
| `manifest.json` | | Schema version, run ID, creation time, Jira version, per-stage coverage. |
| `checkpoint.json` | | Per-stage status, counts, safe error type and HTTP status, resume progress. |

Stages that need earlier stages (`project_workflow_schemes`, the view screens, the screen children) report `not_queried` until their prerequisite is `complete`.

## Record format

Schema version 1.

| Key | Meaning |
| --- | --- |
| `schema_version` | `1`. |
| `source.system` | `jira`. |
| `source.entity` | Entity name, such as `field` or `screen`. |
| `source.id` | Source identifier. Composite IDs join parts with `:`. |
| `source.endpoint` | The read-only route used. |
| `extracted_at` | UTC time of the export, ISO 8601. |
| `raw` | The complete source response as Jira returned it. A record assembled from more than one response keeps each response under its own key. |
| other keys | A normalized view of selected properties, named for the entity. |

Records are written in the order Jira returned them, except that `fields.jsonl` and each page or unit's own records are sorted by ID.

### Duplicate source IDs

The duplicate-source-ID policy is **first occurrence wins**. If a stage receives the same `source.id` more than once — in one page or unit, in a later unit, or after a resume — only the first record is written; later records with that ID are ignored. The order of the remaining records is unchanged. Every stage applies this policy.

## Coverage statuses

Reported in `manifest.json`, `checkpoint.json`, and the final table.

| Status | Meaning |
| --- | --- |
| `complete` | Query succeeded. `complete (0 records, not found)` is a successful empty result, never a gap. |
| `not_queried` | A prerequisite stage did not complete. |
| `unsupported` | No supported read-only API is known, or Jira answered 404, 405, or 501. This is unknown coverage, not an absence of data. Every declared gap below is `unsupported`. |
| `unauthorized` | Jira answered 401 or 403. |
| `failed` | The request or response failed. Committed records are kept and `records_written` is reported. |

### Declared gaps

These sources are known and in scope, but no supported read-only route was identified. Each appears in `manifest.json` coverage as `unsupported`, with the same `reason` and `evidence` shown here. They are advisory: a declared gap never causes a nonzero exit status on its own.

| Declared gap | Reason | Evidence |
| --- | --- | --- |
| `field_contexts` | no supported read-only Jira DC field context API identified | The bundled Jira DC 11.2.1 OpenAPI document defines the FieldConfigScheme bean but no read route; GET /rest/api/2/field/{id}/configschemes returned 404 on Jira DC 11.3.12, and that instance's application.wadl route lists for /rest/api/2 and /rest/projectconfig/1 contain no context route. |
| `field_configurations` | no supported read-only Jira DC field configuration API identified | The only published route that exposes per-issue-type field settings, GET /rest/projectconfig/1/issuetype/{project}/{issueType}/fields, returns the view screen collected by project_issue_type_view_screens; the bundled spec has no field configuration or field configuration scheme read route. |
| `screen_schemes` | no supported read-only Jira DC screen scheme API identified | Neither the bundled Jira DC 11.2.1 OpenAPI document nor the application.wadl route lists published by a Jira DC 11.3.12 instance contain a read route for screen schemes. |
| `issue_type_screen_schemes` | no supported read-only Jira DC issue type screen scheme API identified | Neither the bundled Jira DC 11.2.1 OpenAPI document nor the application.wadl route lists published by a Jira DC 11.3.12 instance contain a read route for issue type screen schemes. |
| `elements_connect` | no supported read-only Elements Connect DC config API identified | The published Elements Connect REST API (SwaggerHub v2.0) renders field content; the field configuration guide documents individual JSON downloads and an admin Import/Export area, not a read-only configuration REST API. |
| `dynamic_forms` | no supported read-only Dynamic Forms DC REST API identified | Fields Visibility exports JSON through the admin UI and the Java ExportConfigurationService, not a REST endpoint; no published OpenAPI configuration-inventory spec was located. |
| `scriptrunner` | no supported read-only ScriptRunner DC REST API identified | Script Registry exports a ZIP/JSON and inventory CSV (documented for 8.54+, 9.19+, 10.0+), and Configuration Exporter is a built-in script; neither is a REST inventory API, and no published OpenAPI configuration spec was located. |

## Exclusions

`fields.jsonl` excludes system fields. No other property is filtered and no value is redacted.
