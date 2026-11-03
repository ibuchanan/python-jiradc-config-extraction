# Development

How to work on the code. To just run the tool, start with the [README](README.md).

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/), which provisions Python 3.14 (`.python-version`, `requires-python >=3.14`).
- A Jira Data Center instance only if you want to run the real export; the tests don't need one.

## Set up

```sh
uv sync
```

This creates `.venv` and installs the workspace packages and the dev tools (`poethepoet`, `pytest`, `ruff`, `ty`).

## Inner loop

Tasks are defined with [Poe the Poet](https://poethepoet.natn.io/) in the root `pyproject.toml`:

| Command | What it runs |
| --- | --- |
| `uv run poe format` | `ruff format .` |
| `uv run poe format-check` | `ruff format --check .` |
| `uv run poe lint` | `ruff check .` |
| `uv run poe typecheck` | `ty check` |
| `uv run poe test` | `pytest` |
| `uv run poe check` | all four checks, in that order |

Run `uv run poe check` before you open a pull request; see [CONTRIBUTING.md](CONTRIBUTING.md) for the review process. To run one test file: `uv run pytest packages/cli/tests/test_resume.py`.

The tests use fake HTTP responses (`httpx` mock transports and fixtures in `packages/cli/tests/conftest.py`), so they never contact Jira.

## Repository structure

This is a uv workspace (`packages/*`) of three packages.

| Path | Role |
| --- | --- |
| `packages/cli` | The `jira-config-export` console script (`cli.main:main`), built with Typer and Rich. `main.py` has the commands and stage orchestration, `doctor.py` the connection checks, `stage_file.py` the crash-safe append-and-checkpoint file handling. |
| `packages/jira` | Async Jira DC client (`client.py`), error mapping (`errors.py`), and per-stage extraction functions (`extraction.py`). Operations are bound to the bundled OpenAPI spec, `src/jira/jira_software_dc_11.2.1_swagger.v3.json`. |
| `packages/config` | Loads the `jira-dc` entry from `.scratch/app-credentials.yaml` into typed settings. |
| `docs/` | User documentation, organized by [Diátaxis](https://diataxis.fr) quadrant. |
| `specs/` | The product specification and research notes. |

Each package keeps its tests in `tests/`. `pytest` is configured at the root to find all three.

## Adding or changing an extraction stage

1. Add an async generator in `packages/jira/src/jira/extraction.py` that yields `Batch` records (one resumable unit each) and wraps HTTP errors as `JiraError`. Build each record with `_record(...)` and include the full source object as `raw`.
2. Call Jira through `client.call("<operationId>", ...)`, using an operation from the bundled OpenAPI spec. An endpoint missing from the spec needs a declared override in the client.
3. Wire the stage into `export_configuration` in `packages/cli/src/cli/main.py`, with its output file name and any prerequisite stage.
4. Add tests at the public seam (`cli.main([...])` with a fake handler) and for the extraction function itself.
5. Update the output-file table in [docs/reference/cli.md](docs/reference/cli.md).

## Things to know

- **Request pacing** is the `REQUESTS_PER_MINUTE` constant (120) near the top of `packages/cli/src/cli/main.py`.
- **Resume safety:** a stage appends whole lines, flushes and `fsync`s, and only then records `{cursor, offset, count}` in `checkpoint.json`. Don't write stage files any other way. The details are in [packages/cli/README.md](packages/cli/README.md).
- **Internal Jira API:** the view-screen stage uses `/rest/projectconfig/1/...`, which isn't documented and is marked `"api": "internal"` in the output.
- **Don't guess endpoints** for apps with no documented read API (Elements Connect, ScriptRunner, Dynamic Forms). Report them as `unsupported`; see [specs/plugin-api-references.md](specs/plugin-api-references.md).
- **The OpenAPI file** is Data Center, not Cloud. Don't replace it with the Cloud document; see [packages/jira/openapi/README.md](packages/jira/openapi/README.md).
- **Credentials** live in `.scratch/`, which Git ignores. Never commit them.

## Troubleshooting

- **`uv sync` can't find Python 3.14:** let `uv` download it (`uv python install 3.14`), or check your network and proxy settings.
- **`doctor` reports `proxy` warn:** the client ignores `HTTPS_PROXY`-style variables by design.
- **A test fails with an unexpected request path:** the fake handler raises on routes it doesn't expect; add the new route to the handler.
