# Provide Jira credentials to the CLI

Use this guide to give `jira-config-export` the connection details for a Jira Data Center instance.

## Create the credentials file

1. Create the file `.scratch/app-credentials.yaml` under the directory you run the command from. The path is fixed and relative to the current directory; there is no option to change it. The `.scratch/` directory is Git-ignored.
2. Add an `apps` list containing one entry named `jira-dc`:

   ```yaml
   apps:
     - name: jira-dc
       url: https://jira.example.com
       user: your-user-name
       password: your-password
   ```

3. Check it:

   ```sh
   uv run jira-config-export doctor
   ```

## Keys the CLI reads

| Key | Value |
| --- | --- |
| `name` | Must be exactly `jira-dc`. The first `apps` entry with this name is used. |
| `url` | Base URL of Jira, including any context path (for example `https://jira.example.com/jira`). |
| `user` | User name for HTTP Basic authentication. |
| `password` | Password for that user. |

All three of `url`, `user`, and `password` must be strings. Other keys, and other entries in `apps`, are ignored.

## Override the URL for one run

`export` takes an optional `--url`:

```sh
uv run jira-config-export export --url https://jira.example.com/jira ./export
```

When it is omitted, the `url` from the credentials file is used. When it is given, it takes precedence over that value, but it must keep the same scheme, host, and port so the credentials are not sent to a different server. A trailing slash is ignored; the path may differ. The URL is validated before the output directory is touched.

Credentials always come from this file. The command has no `--user` or `--password` option, and a `--url` that embeds a user name or password is rejected.

## If you want to keep other applications in the same file

Add more entries to `apps` with different names. Only the `jira-dc` entry is read.

## If `doctor` fails

- `config` fails: the file is missing, `apps` is not a list, there is no `jira-dc` entry, or a required key is missing or not a string.
- `auth` fails: the user or password is wrong, or the account lacks permission to call `GET /rest/api/2/myself`.
- `proxy` warns: `HTTPS_PROXY`-style variables are set, and the exporter ignores them.

The output of `doctor` never includes the password.
