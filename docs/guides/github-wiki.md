# Publishing the GitHub Wiki

The repository's Wiki tab is **generated** from `docs/` (the manual, the guides, the architecture notes and the ADRs).
Edit the files in `docs/`, never the wiki: the next sync overwrites it. The same pages are also available inside the
web page (**? Help**), see [the web page](../manual/05-web-page.md).

## First time

GitHub creates the wiki's git repository only when its first page is saved in the browser:

1. Open the repository on GitHub, then the **Wiki** tab, then **Create the first page**, and save it with any text.
2. Run the sync (below). It replaces that page with the generated ones.

## Automatic publishing

A GitHub Actions workflow (`.github/workflows/wiki.yml`) publishes the wiki after every push to `dev` that changes
`docs/`, `scripts/sync_wiki.py` or `webui/wiki.py`, using the repository's own `GITHUB_TOKEN`. Check the run in the
repository's **Actions** tab. To publish from another state, run the workflow by hand (**Run workflow**), or use the
command below.

## Every time the docs change (by hand, when CI is not an option)

```bash
python scripts/sync_wiki.py --push
```

It clones the wiki repository, replaces its pages, and commits and pushes only when something changed. It prints the
page count, and never the access token. If the wiki repository does not exist yet it says so and what to do.

To look at the result without publishing:

```bash
python scripts/sync_wiki.py --out /tmp/wiki-preview
```

## What you get

| In the wiki | From |
|---|---|
| `Home` | the manual's start page |
| `Manual-NN-...` | `docs/manual/` |
| `Guide-...` | `docs/guides/` |
| `Architecture-...` | `docs/architecture/` |
| `ADR-NNNN-...` | `docs/adr/` |
| `_Sidebar`, `_Footer` | generated: navigation and a "generated from docs/" note |

Links between pages become wiki links, images are copied to `images/`, and a link to a file outside `docs/` (for
example `SPEC.md`) opens that file in the repository.
