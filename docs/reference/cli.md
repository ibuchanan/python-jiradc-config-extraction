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

Exit status: `0` on success; `1` if preflight fails, a queried stage fails, is unauthorized, or is unsupported, or an I/O error occurs. Static `unsupported` gaps for apps do not alone cause a nonzero status.

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
| `raw` | The complete source object as Jira returned it. |
| other keys | A normalized view of selected properties, named for the entity. |

Records are written in the order Jira returned them, except that `fields.jsonl` and each page or unit's own records are sorted by ID. Duplicate source IDs within a stage are written once; the first wins.

## Coverage statuses

Reported in `manifest.json`, `checkpoint.json`, and the final table.

| Status | Meaning |
| --- | --- |
| `complete` | Query succeeded. `complete (0 records, not found)` is a successful empty result. |
| `not_queried` | A prerequisite stage did not complete. |
| `unsupported` | No supported read-only API is known, or Jira answered 404, 405, or 501. This is unknown coverage, not an absence of data. |
| `unauthorized` | Jira answered 401 or 403. |
| `failed` | The request or response failed. Committed records are kept and `records_written` is reported. |

Statically `unsupported`: `elements_connect`, `scriptrunner`, and `dynamic_forms`, plus field contexts, field configurations, screen schemes, and issue type screen schemes, which are not collected.

## Exclusions

`fields.jsonl` excludes system fields. No other property is filtered and no value is redacted.
