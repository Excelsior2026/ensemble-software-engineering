# Release Reporting Plugin

Example external ESE reporting plugin that contributes:

- a `blocker-csv` report exporter
- a `release-brief` virtual artifact view

## Development

```bash
pip install -e .
ese exporters
ese views
```

After installation, the plugin extends both `ese export --format blocker-csv` and the dashboard artifact viewer.
