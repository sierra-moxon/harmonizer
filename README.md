# harmonizer

Iterative agent that maps arbitrary spreadsheets to the
[`nmdc-submission-schema`](https://github.com/microbiomedata/submission-schema).

A deterministic pre-pass drafts a mapping with placeholders for anything it
cannot resolve; an iterative agent loop then resolves each placeholder with
evidence (schema slot lookup, ontology resolution via OAK/`runoak`) or
explicitly refuses. Output is both a schema-conformant artifact and a curation
report auditing every placeholder outcome.

See [docs/SPREADSHEET_MAPPING_AGENT_PLAN.md](docs/SPREADSHEET_MAPPING_AGENT_PLAN.md)
for the full implementation plan.

## Development

Requires [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/).

```sh
just install   # sync dependencies
just test      # run the test suite
```
