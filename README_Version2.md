# Bulk update org URLs (Windows/Python)

This tool replaces occurrences of:
- `https://github.com/TensoRaws`
with:
- `https://github.com/EutropicAI`

across repositories in the `EutropicAI` organization. It creates a branch, commits the changes, pushes, and opens a Pull Request per repo. A dry-run mode is available.

## Prerequisites
- Windows with Git installed and available on PATH
- Python 3.9+
- GitHub token with repo push access:
  - Set environment variable `GITHUB_TOKEN` (PowerShell: `$env:GITHUB_TOKEN="..."`; CMD: `set GITHUB_TOKEN=...`)
- Install Python deps:
  ```bash
  pip install -r requirements.txt
  ```

## Quick start

Dry-run first (no changes pushed, just reports):
```bash
python scripts/bulk_update_org_urls.py --dry-run
```

Actually perform changes and open PRs:
```bash
python scripts/bulk_update_org_urls.py
```

## Common options

- `--org` Organization name (default: `EutropicAI`)
- `--old` Old URL (default: `https://github.com/TensoRaws`)
- `--new` New URL (default: `https://github.com/EutropicAI`)
- `--only-public` Only process public repos
- `--include-archived` Include archived repos
- `--limit N` Max repos to process (default: 1000)
- `--repos name1 name2 ...` Only process given repo names within the org
- `--branch-prefix` Branch name prefix (default: `chore/update-org-urls`)
- `--sleep N` Seconds to sleep between repos (default: 2)
- `--dry-run` Do not commit/push/create PR, only report files and planned changes
- `--confirm` Require Y/N confirmation before committing per repo

Examples:
```bash
# Only public repos
python scripts/bulk_update_org_urls.py --only-public

# Target a subset
python scripts/bulk_update_org_urls.py --repos AnimeSR txt2epub2

# Custom message and slower pacing
python scripts/bulk_update_org_urls.py --sleep 5
```

## Notes
- GitHub provides automatic redirects after org/repo rename, so functionality typically won’t break. Still, updating explicit links in README/docs/badges/submodules is recommended for clarity.
- The script skips common binary/assets/build paths and only modifies text files (tries UTF-8 first, falls back to Latin-1).
- `.gitmodules` will be updated when it contains the old URL.
- If you also have GHCR or other namespaces (like `ghcr.io/TensoRaws/...`), run again with adjusted `--old/--new`.

## Rollback
- Close PRs or delete the branch:
```
git push origin --delete chore/update-org-urls/from-TensoRaws-to-EutropicAI
```