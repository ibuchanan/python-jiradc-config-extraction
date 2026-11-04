# Jira DC config extraction

A read-only command-line tool that exports the configuration of a Jira Data Center instance — custom fields, options, screens, projects, issue types, schemes — into a local folder of JSON Lines files you can analyze.

It was built to help migration analysts see how custom fields (especially Elements Connect fields) are used, without needing a database dump. Each record keeps the complete response (or responses) Jira returned, plus its source and extraction time, so you can trace any finding back to evidence.

## Is this for you?

Use it if you can log in to Jira Data Center with an account that can read its configuration, and you want a readable, restartable inventory of what Jira exposes through its REST API.

It is not a migration verdict, and it does not see everything. Seven sources are reported in `manifest.json` coverage as `unsupported` gaps with a reason and evidence, not collected: `field_contexts`, `field_configurations`, `screen_schemes`, `issue_type_screen_schemes`, `elements_connect`, `dynamic_forms`, and `scriptrunner`. A gap means "unknown", not "none". Where Jira's plugin manager is readable, it does record which user-installed apps are present and their versions, and links a field to an app by its exact type key; that is presence evidence, not app configuration. See [why this isn't a database dump](docs/explanation/why-not-a-database-dump.md) and the [recorded scope decisions](specs/SPEC.md#14-recorded-scope-decisions).

## Quickstart

You need [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/). `uv` downloads Python 3.14 for you.

```sh
git clone https://github.com/ibuchanan/python-jiradc-config-extraction.git
cd python-jiradc-config-extraction
uv sync
```

Tell the tool where Jira is, in the Git-ignored `.scratch/app-credentials.yaml`:

```yaml
apps:
  - name: jira-dc
    url: https://jira.example.com
    user: your-user-name
    password: your-password
```

Check the connection, then export:

```sh
uv run jira-config-export doctor
uv run jira-config-export export ./export
```

You'll see one dot per record written, then a coverage table. The folder now holds files such as `fields.jsonl` and `screens.jsonl`, plus `manifest.json` (what was and wasn't collected). Run the same `export` command again and finished stages are skipped, so an interrupted run resumes where it stopped.

## What you get

- **Read-only.** Only `GET` requests; nothing in Jira changes.
- **Restartable.** Progress is saved after every unit of work.
- **Detected apps, not app configuration.** Installed user-installed apps and their versions are recorded from Jira's plugin manager when it is readable, and fields are linked to an app by exact type key. App configuration itself is a reported gap.
- **Honest about gaps.** Each stage is marked `complete`, `not_queried`, `unsupported`, `unauthorized`, or `failed` in `manifest.json`.
- **Verbatim.** Configuration is written as Jira returns it, with no redaction, so store the output as carefully as you would the Jira configuration itself.

## Documentation

- [Tutorial: get the exporter running](docs/tutorials/getting-started.md)
- [How-to: provide Jira credentials](docs/how-to/provide-credentials.md)
- [Reference: CLI commands, options, and output files](docs/reference/cli.md)
- [Explanation: why not a database dump](docs/explanation/why-not-a-database-dump.md)
- [Product specification](specs/SPEC.md)

## Status and limitations

Early (version 0.1.0). Authentication is HTTP Basic only; personal access tokens aren't supported yet. The client targets the Jira Software Data Center 11.2.1 API.

## Contributing

Pull requests and issues are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md) and the [code of conduct](CODE_OF_CONDUCT.md). To change the code, start with [DEVELOPMENT.md](DEVELOPMENT.md). Questions and bug reports go to the [issue tracker](https://github.com/ibuchanan/python-jiradc-config-extraction/issues).

## License

Apache License 2.0. See [LICENSE](LICENSE).
