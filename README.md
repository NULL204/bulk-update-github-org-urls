# Bulk update GitHub org URLs across all repositories

This toolkit replaces occurrences of `https://github.com/TensoRaws` with `https://github.com/EutropicAI` across repositories in the `EutropicAI` organization, committing changes on a branch and opening Pull Requests automatically.

## Prerequisites

- GitHub CLI: `gh auth login` (token needs `repo` scope and org access)
- `git`, `jq`, `perl`
- Optional but recommended: `ripgrep (rg)` for fast and accurate file discovery

## Quick start

```bash
# dry-run: list files that would change, do not commit or PR
DRY_RUN=true bash scripts/bulk-update-github-org-urls.sh

# actually perform changes and open PRs
bash scripts/bulk-update-github-org-urls.sh
```

## Useful options (env vars)

- `ORG` (default `EutropicAI`)
- `OLD` (default `https://github.com/TensoRaws`)
- `NEW` (default `https://github.com/EutropicAI`)
- `DRY_RUN` = `true|false`
- `LIMIT` = max number of repos to process (default `1000`)
- `ONLY_PUBLIC` = `true` to only process public repos
- `INCLUDE_ARCHIVED` = `true` to include archived repos
- `TARGET_REPOS` = space-separated list to limit repos, e.g. `"AnimeSR another-repo"`

Examples:

```bash
# Only public repos
ONLY_PUBLIC=true bash scripts/bulk-update-github-org-urls.sh

# Target a subset
TARGET_REPOS="AnimeSR txt2epub2" bash scripts/bulk-update-github-org-urls.sh
```

## Notes and best practices

- GitHub provides automatic redirects when orgs/repos are renamed. In many cases, code continues to work. Still, updating links improves clarity and avoids user confusion.
- Submodules and documentation links should be updated; redirects are followed but explicit URLs are cleaner.
- The script skips common binary/asset/build paths and only modifies text files.
- To review impact beforehand, run GitHub code search:
  - `org:EutropicAI "https://github.com/TensoRaws"`

## Rollback

If needed, close PRs or delete the branch:
```
git push origin --delete chore/update-org-urls/https://github.com/TensoRaws-to-https://github.com/EutropicAI
```