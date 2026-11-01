# zombiepapers

Check your bibliography for retracted and corrected papers, and find out what is wrong with them.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

zombiepapers fetch retraction-watch     # clone (or update) the Retraction Watch data
zombiepapers ingest retraction-watch    # load it into data/zombiepapers.duckdb

zombiepapers check refs.bib             # check a whole bibliography
zombiepapers check-doi 10.1234/abc      # or one or more DOIs (any form: doi:, https://doi.org/...)
```

Quote DOIs that contain parentheses, e.g. `zombiepapers check-doi '10.1016/S0140-6736(97)11096-0'`,
or the shell will try to interpret them. Add `--json` to `check` or `check-doi` for machine-readable output.

Run the tests with `pytest`.

## OpenAlex (optional)

With an [OpenAlex API key](https://openalex.org) in `OPENALEX_API_KEY`, entries are completed by
OpenAlex (entries without a DOI are resolved by title) and OpenAlex's retraction flag is used as a
second signal. It catches about a quarter of retracted papers that Retraction Watch does not list
yet, mostly recent ones. Responses are cached in the database, so repeated runs are free.

Without a key, `check` runs offline against Retraction Watch only and says so in a warning.

## Exit codes

| code | meaning |
|---|---|
| 0 | nothing flagged |
| 1 | at least one retracted paper or expression of concern (always wins) |
| 2 | wrong usage (e.g. not a DOI) |
| 75 | incomplete: OpenAlex unavailable after retries; try again later |
| 78 | fix something first: no data ingested, or OpenAlex refused the request (e.g. invalid key) |

## Other commands

- `zombiepapers resolve refs.bib`: show how each entry was identified (DOI, PubMed ID, title search).
- `zombiepapers taxonomy unmapped`: list retraction reasons not yet mapped to an explanation.
- `zombiepapers init-db`: create or migrate the database.

## Data sources

- [Retraction Watch database](https://gitlab.com/crossref/retraction-watch-data) (via Crossref)
- [OpenAlex](https://openalex.org)
