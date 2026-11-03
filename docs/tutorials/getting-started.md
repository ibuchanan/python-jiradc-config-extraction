# Get the exporter running

In this tutorial we will install the project, check that its tests pass, and run a first export against a Jira Data Center instance. It takes about ten minutes. You need an account on that instance that can read its configuration.

## Before you start

Install these first:

- [Git](https://git-scm.com/downloads)
- [uv](https://docs.astral.sh/uv/getting-started/installation/), the Python package and project manager. We will not install Python ourselves; `uv` fetches the version this project needs.
- The base URL, user name, and password for your Jira Data Center instance.

## 1. Get the code

Clone the repository and move into it:

```sh
git clone https://github.com/ibuchanan/python-jiradc-config-extraction.git
cd python-jiradc-config-extraction
```

## 2. Install the dependencies

```sh
uv sync
```

You'll notice that `uv` creates a `.venv` directory and, if needed, downloads Python 3.14.

## 3. Run the tests

```sh
uv run pytest
```

The run ends with a line such as `82 passed`. The tests use fake responses, so they do not contact Jira.

## 4. Tell the tool where Jira is

Create the folder `.scratch` and a file inside it named `app-credentials.yaml`:

```sh
mkdir -p .scratch
```

Put this in `.scratch/app-credentials.yaml`, replacing the three values with your own:

```yaml
apps:
  - name: jira-dc
    url: https://jira.example.com
    user: your-user-name
    password: your-password
```

The `.scratch` folder is ignored by Git, so these values will not be committed.

## 5. Check the connection

```sh
uv run jira-config-export doctor
```

A table appears with the checks `config`, `url`, `dns`, `tcp`, `http`, `auth`, and `proxy`. Each shows `pass` when it works. If one shows `fail`, the checks after it show `skip`; fix that one and run the command again.

## 6. Run your first export

```sh
uv run jira-config-export export ./export
```

First you'll see a one-line preflight result and the absolute path of the output directory. Then each stage prints one dot for every record it writes. When the run finishes, a coverage table lists every stage with its status.

## 7. Look at the result

```sh
ls ./export
head -n 1 ./export/fields.jsonl
```

You now have one JSON record per line in files such as `fields.jsonl` and `screens.jsonl`, plus `manifest.json` and `checkpoint.json`.

Run the same `export` command again. The stages that are already complete print a `Skipping` line instead of fetching again.

## What we did

We installed the project, proved it works with its tests, confirmed that it can reach Jira, and produced a first set of configuration files. Next, see how to [provide credentials](../how-to/provide-credentials.md) in other ways, and consult the [CLI reference](../reference/cli.md) for every command and output file.
