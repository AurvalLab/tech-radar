# Tech Radar

A lightweight, dependency-free engineering intelligence radar for the AI agent, MCP, CLI, and automation ecosystem.

It queries GitHub repositories and Hacker News, scores each candidate across relevance, engineering value, maturity, freshness, and specificity, then emits a small ranked batch. The default rules intentionally reject toy projects, tutorials, generic keyword stacks, and low-signal collections.

## Features

- GitHub discovery for new projects, fast-growing repositories, and curated collections
- Hacker News discovery for recent engineering discussions
- 0-100 weighted scoring model with penalties, caps, and high-value rescue rules
- URL and ID deduplication across sources and previous deliveries
- Preview/commit delivery flow so downstream notification failures do not consume items
- JSON and human-readable output modes
- Standard-library-only Python implementation

## Requirements

- Python 3.9+
- Network access to GitHub and Hacker News
- Optional: authenticated GitHub CLI (`gh`) for higher API limits

## Quick Start

```bash
python tech_radar.py --show --json
```

Preview results without consuming them. When your delivery step succeeds, commit the previewed batch:

```bash
python tech_radar.py --commit
```

Until committed, later `--show` calls replay the same pending batch. To discard
that batch and fetch a fresh preview explicitly:

```bash
python tech_radar.py --show --refresh --json
```

For a simple one-step run that prints and marks the selected items as seen:

```bash
python tech_radar.py
```

## Safe Delivery Pattern

Use `--show` before sending results to Slack, email, a webhook, or any other destination:

```bash
python tech_radar.py --show --json > batch.json
# Deliver batch.json through your own integration.
python tech_radar.py --commit
```

If delivery fails, do not call `--commit`; the same batch remains pending and the
next `--show` replays it. This prevents a retry from silently replacing the batch.

## Configuration

All configuration is optional and uses environment variables.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TECH_RADAR_DATA_DIR` | OS user state directory | Directory for state and pending files |
| `TECH_RADAR_STATE` | `<data-dir>/state.json` | Explicit state file path |
| `TECH_RADAR_PENDING` | `<data-dir>/pending.json` | Explicit pending-batch path |
| `TECH_RADAR_ENV_FILE` | unset | Optional dotenv-format file to load |
| `GITHUB_TOKEN` / `GH_TOKEN` | unset | Optional GitHub API token |
| `TECH_RADAR_PASS_THRESHOLD` | `60` | Primary delivery threshold |
| `TECH_RADAR_CANDIDATE_THRESHOLD` | `52` | Fallback candidate threshold |
| `TECH_RADAR_MAX_PER_TICK` | `5` | Maximum selected items |

Copy `example.env` to a private `.env` file if desired, then point `TECH_RADAR_ENV_FILE` at it. Do not commit tokens or state files.

## Testing

```bash
python -m unittest discover -s tests -v
python -m py_compile tech_radar.py
```

## License

MIT. See [LICENSE](LICENSE).
