# dv-beef-exports

Market-intelligence tool for a Brazilian beef export trading company,
built on [ComexStat](https://comexstat.mdic.gov.br) trade data.

The company acts as an intermediary between suppliers and buyers, and
finding new buyers is its main bottleneck. Paid trade-intelligence
platforms and exhibition booths are out of budget, so this project builds
a lightweight, in-house alternative: answering "what is Brazil selling,
to where, and which markets look promising" from Brazil's own public
foreign-trade statistics — as a first step toward identifying actual
importer companies to reach out to.

See `docs/ROADMAP.md` for the full plan and `docs/decisions/` for why
things are built the way they are.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras --dev
```

## Development

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run ruff format .   # format
```

Install the pre-commit hooks once, so lint/format run automatically on
commit:

```bash
uv run pre-commit install
```

See `docs/WORKFLOW.md` for branching/PR conventions.
