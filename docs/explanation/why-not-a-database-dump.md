# Why export through the REST API instead of a database dump

This page explains what kind of evidence `jira-config-export` produces, and when it is a better choice than a dump of the Jira Data Center database. It does not describe how to run the tool; see the [tutorial](../tutorials/getting-started.md) for that.

## What each approach gives you

A database dump is a complete copy of everything Jira stores: configuration, issues, comments, users, and the tables that apps keep for themselves. It is the most complete source there is. It is also the least convenient one for answering a narrow question such as "which screens carry this custom field, and which projects reach those screens?" The answer is spread across many tables whose layout is an internal detail of the Jira version that wrote them.

`jira-config-export` takes the opposite trade. It asks Jira's own read-only HTTP interface for configuration only, and writes each answer as a JSON record. The records are a view Jira chose to expose, not a copy of its storage. That costs completeness and buys four things.

## Reasons to prefer the export

**You may not be able to get a dump.** Producing one usually needs access to the database host or the people who run it. The export needs only a Jira account that can read the configuration, which is the access a migration analyst is most likely to have.

**It stays inside what Jira supports.** The client only issues `GET` requests, so a run cannot change Jira. It also reads through documented operations, so the records mean what Jira's API says they mean, rather than what an analyst infers from a table. The exception is one internal route, used for view screens, which is marked `"api": "internal"` in the output so you know it carries more risk of changing.

**The output is shaped for analysis, not restoration.** Every line is a self-contained JSON record with a source system, entity, ID, endpoint, and extraction time. Related records join on stable IDs. Each record also keeps the complete source response under `raw` — or both responses, when one record joins two — so nothing Jira returned is thrown away. A dump has to be loaded into a database of the right version before you can query it.

**It is honest about what it missed.** A dump is silent about gaps: if a table is empty you cannot tell whether nothing is configured or whether you read the wrong table. The export records a coverage status for each stage in `manifest.json`: `complete`, `not_queried`, `unsupported`, `unauthorized`, or `failed`. A stage marked `unsupported` means "this tool could not look", not "there is nothing there".

The export is also restartable. A large instance can take a long time at a polite request rate, and an interruption keeps every committed unit of work. A dump is all-or-nothing in the sense that a failed copy usually starts again.

## What you give up

The export cannot see what Jira does not publish. At the time of writing it does not collect field contexts, field configurations (hidden and required settings), screen schemes, or issue type screen schemes, because no read route for them was found. It does not collect app configuration either: Elements Connect, ScriptRunner, and Dynamic Forms each document an administration-screen export, not a configuration API. A database dump would contain much of this.

So the two are complements. Use the export to build a readable, provenance-tracked inventory of the configuration Jira exposes, and to find out which questions the API can answer. Reach for a dump, or for an app's own export from its admin screen, when a question depends on data in the gaps the manifest reports. See the [CLI reference](../reference/cli.md) for how the coverage statuses appear.

## A note on sensitivity

The export copies configuration as Jira returns it and applies no redaction. That is a deliberate decision: the data is treated as metadata. It follows that the output directory is only as safe as the access you give it, and that app configuration pulled in later, such as a datasource definition, may contain connection details. Decide where the files live before you run the tool.
