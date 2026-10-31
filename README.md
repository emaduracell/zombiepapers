# zombiepapers

Check your bibliography for retracted and corrected papers, and find out what is wrong with them.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
zombiepapers init-db
```

Run the tests with `pytest`.

## Data sources

- [Retraction Watch database](https://gitlab.com/crossref/retraction-watch-data) (via Crossref)
- [OpenAlex](https://openalex.org)
