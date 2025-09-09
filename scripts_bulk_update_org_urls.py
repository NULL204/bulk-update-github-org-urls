#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Bulk operations:
  1) Replace a specific old org base URL with a new org base URL.
  2) Convert absolute LICENSE links (blob or raw) to relative ./LICENSE:
       Blob: https://github.com/(TensoRaws|EutropicAI)/Repo/blob/<branch>/LICENSE(.md)?
       Raw : https://raw.githubusercontent.com/(TensoRaws|EutropicAI)/Repo/<ref>/LICENSE(.md)?
     Any label containing 'license' (case-insensitive) is preserved.
With auto-fork fallback / always-fork option when lacking push rights.
"""

import argparse
import os
import sys
import tempfile
import time
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple, Optional, Dict
import re

from github import Github, GithubException, Auth

# ---------------- Configuration sets ---------------- #

DEFAULT_SKIP_DIRS = {
    ".git",
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "out",
    "target",
}

BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".otf", ".ico",
    ".mp4", ".mp3", ".avi", ".mov", ".mkv",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".class", ".o", ".a", ".lib", ".obj",
    ".pkl", ".pt", ".onnx", ".pb",
}

TEXT_LIKE_EXTS = {
    ".md", ".markdown", ".txt", ".rst",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
    ".java", ".go", ".rb", ".rs", ".cpp", ".cxx", ".cc", ".c", ".h", ".hpp",
    ".cs", ".kt", ".swift",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".html", ".htm", ".css", ".scss", ".less",
    ".Dockerfile", ".dockerignore", ".gitignore", ".gitattributes",
    ".tf", ".tfvars",
    ".csv", ".tsv",
    ".gradle", ".properties",
}

# ---------------- Utility functions ---------------- #

def run(cmd: List[str], cwd: Optional[Path] = None, check: bool = True,
        capture_output: bool = False, text: bool = True):
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=capture_output,
        text=text
    )


def have_git() -> bool:
    try:
        run(["git", "--version"], check=True, capture_output=True)
        return True
    except Exception:
        return False


def sanitize_branch_name(s: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._/-" else "-" for ch in s)
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe.strip("-/") or "update-urls"


def is_probably_text_file(path: Path) -> bool:
    ext = path.suffix
    if ext.lower() in BINARY_EXTS:
        return False
    try:
        with open(path, "rb") as f:
            chunk = f.read(4096)
        if b"\x00" in chunk:
            return False
    except Exception:
        return False
    return True


def read_text_with_fallback(p: Path) -> Tuple[Optional[str], Optional[str]]:
    for enc in ("utf-8", "latin-1"):
        try:
            return p.read_text(encoding=enc), enc
        except Exception:
            continue
    return None, None

# ---------------- LICENSE link regex builders ---------------- #

def build_blob_license_regex(repo_name: str) -> re.Pattern:
    """
    Matches markdown links:
      [any license-y label](https://github.com/(TensoRaws|EutropicAI)/RepoName/blob/<ref>/LICENSE(.md)?)
    capturing:
      group1: label
      group2: LICENSE / LICENCE (base)
    """
    pattern = (
        r'\[([^\]]*?licen[cs]e[^\]]*?)\]\('
        r'https://github\.com/(?:TensoRaws|EutropicAI)/'
        + re.escape(repo_name) +
        r'/blob/[A-Za-z0-9._\-/]+/'
        r'(LICENSE|LICENCE)(?:\.md)?'
        r'\)'
    )
    return re.compile(pattern, re.IGNORECASE)


def build_raw_license_regex(repo_name: str) -> re.Pattern:
    """
    Matches markdown links:
      [label](https://raw.githubusercontent.com/(TensoRaws|EutropicAI)/RepoName/<ref>/LICENSE(.md)?)
    Similar capturing:
      group1: label
      group2: LICENSE / LICENCE
    """
    pattern = (
        r'\[([^\]]*?licen[cs]e[^\]]*?)\]\('
        r'https://raw\.githubusercontent\.com/(?:TensoRaws|EutropicAI)/'
        + re.escape(repo_name) +
        r'/[A-Za-z0-9._\-/]+/'
        r'(LICENSE|LICENCE)(?:\.md)?'
        r'\)'
    )
    return re.compile(pattern, re.IGNORECASE)

# ---------------- Scanning & Replacing ---------------- #

def scan_repo(repo_dir: Path, old: str, repo_name: str,
              convert_license_links: bool) -> Dict[str, Dict[str, int]]:
    """
    Scan only. Return mapping:
      file_path -> {
         'old_hits': n_old_url,
         'license_blob': n_blob_links,
         'license_raw': n_raw_links
      }
    """
    results: Dict[str, Dict[str, int]] = {}
    old_bytes = old.encode("utf-8", errors="ignore")
    blob_regex = build_blob_license_regex(repo_name) if convert_license_links else None
    raw_regex = build_raw_license_regex(repo_name) if convert_license_links else None

    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in DEFAULT_SKIP_DIRS]
        for fname in files:
            path = Path(root) / fname
            try:
                if path.stat().st_size > 5 * 1024 * 1024:
                    continue
            except Exception:
                continue

            ext = path.suffix
            looks_texty = ext in TEXT_LIKE_EXTS
            if not looks_texty and not is_probably_text_file(path):
                continue

            try:
                with open(path, "rb") as fb:
                    data = fb.read()
            except Exception:
                continue

            old_hits = data.count(old_bytes) if old_bytes in data else 0
            need_text = old_hits > 0

            blob_hits = 0
            raw_hits = 0
            # Quick heuristic: if license keywords maybe appear
            if convert_license_links and (b"LICENSE" in data.upper() or b"LICENCE" in data.upper()):
                need_text = True

            if not need_text:
                continue

            text, _enc = read_text_with_fallback(path)
            if text is None:
                continue

            if convert_license_links:
                if blob_regex:
                    blob_hits = len(blob_regex.findall(text))
                if raw_regex:
                    raw_hits = len(raw_regex.findall(text))

            if old_hits == 0 and blob_hits == 0 and raw_hits == 0:
                continue

            results[str(path)] = {
                "old_hits": old_hits,
                "license_blob": blob_hits,
                "license_raw": raw_hits
            }

    return results


def replace_old_urls(text: str, old: str, new: str) -> Tuple[str, int]:
    count = text.count(old)
    if count:
        text = text.replace(old, new)
    return text, count


def convert_license_links(text: str, repo_name: str) -> Tuple[str, int, int]:
    """
    Convert blob + raw license links to relative './LICENSE'.
    Returns (new_text, blob_converted_count, raw_converted_count)
    """
    blob_re = build_blob_license_regex(repo_name)
    raw_re = build_raw_license_regex(repo_name)

    def blob_repl(m: re.Match) -> str:
        label = m.group(1)
        return f'[{label}](./LICENSE)'

    def raw_repl(m: re.Match) -> str:
        label = m.group(1)
        return f'[{label}](./LICENSE)'

    new_text, blob_n = blob_re.subn(blob_repl, text)
    newer_text, raw_n = raw_re.subn(raw_repl, new_text)
    return newer_text, blob_n, raw_n


def apply_replacements(repo_dir: Path, old: str, new: str, repo_name: str,
                       do_license: bool) -> Tuple[List[Tuple[Path, int, int, int]], int, int, int]:
    """
    Returns:
      per_file: list of (Path, old_repl, blob_conv, raw_conv)
      old_total, blob_total, raw_total
    """
    per_file: List[Tuple[Path, int, int, int]] = []
    old_total = 0
    blob_total = 0
    raw_total = 0

    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in DEFAULT_SKIP_DIRS]
        for fname in files:
            path = Path(root) / fname
            try:
                if path.stat().st_size > 5 * 1024 * 1024:
                    continue
            except Exception:
                continue

            ext = path.suffix
            looks_texty = ext in TEXT_LIKE_EXTS
            if not looks_texty and not is_probably_text_file(path):
                continue

            text, enc = read_text_with_fallback(path)
            if text is None:
                continue

            orig = text
            old_cnt = 0
            blob_cnt = 0
            raw_cnt = 0

            if old in text:
                text, old_cnt = replace_old_urls(text, old, new)

            if do_license:
                text, blob_cnt, raw_cnt = convert_license_links(text, repo_name)

            if text != orig:
                try:
                    path.write_text(text, encoding=enc or "utf-8")
                    if old_cnt or blob_cnt or raw_cnt:
                        per_file.append((path, old_cnt, blob_cnt, raw_cnt))
                        old_total += old_cnt
                        blob_total += blob_cnt
                        raw_total += raw_cnt
                except Exception:
                    continue

    return per_file, old_total, blob_total, raw_total

# ---------------- Git helpers ---------------- #

def ensure_branch(cwd: Path, default_branch: str, branch: str):
    run(["git", "checkout", default_branch], cwd=cwd)
    try:
        run(["git", "fetch", "origin", default_branch], cwd=cwd, check=False)
    except Exception:
        pass
    res = run(["git", "rev-parse", "--verify", branch], cwd=cwd,
              check=False, capture_output=True)
    if res.returncode == 0:
        run(["git", "checkout", branch], cwd=cwd)
    else:
        run(["git", "checkout", "-b", branch, f"origin/{default_branch}"], cwd=cwd)


def commit_all(cwd: Path, message: str) -> bool:
    run(["git", "add", "-A"], cwd=cwd)
    res = run(["git", "diff", "--cached", "--quiet"], cwd=cwd, check=False)
    if res.returncode == 0:
        return False
    run(["git", "commit", "-m", message], cwd=cwd)
    return True


def push_branch_to_remote(cwd: Path, remote: str, branch: str) -> bool:
    res = run(["git", "push", "-u", remote, branch], cwd=cwd, check=False)
    return res.returncode == 0


def add_or_update_remote(cwd: Path, name: str, url: str):
    res = run(["git", "remote"], cwd=cwd, check=False, capture_output=True)
    remotes = (res.stdout or "").split()
    if name in remotes:
        run(["git", "remote", "set-url", name, url], cwd=cwd)
    else:
        run(["git", "remote", "add", name, url], cwd=cwd)


def https_url(owner_repo: str, token: Optional[str]) -> str:
    if token:
        return f"https://{token}:x-oauth-basic@github.com/{owner_repo}.git"
    return f"https://github.com/{owner_repo}.git"


def clone_repo(owner_repo: str, dest: Path, token: Optional[str]) -> bool:
    url = https_url(owner_repo, token)
    res = run(["git", "clone", "--depth", "1", url, str(dest)], check=False)
    return res.returncode == 0


def can_push_to_repo(repo, my_login: Optional[str]) -> bool:
    try:
        perms = getattr(repo, "permissions", None)
        if isinstance(perms, dict):
            return bool(perms.get("push") or perms.get("admin"))
    except Exception:
        pass
    try:
        return repo.owner.login == my_login if my_login else False
    except Exception:
        return False


def ensure_fork(gh: Github, upstream_repo, my_login: str,
                timeout_sec: int = 120):
    fork_full = f"{my_login}/{upstream_repo.name}"
    fork_repo = None
    created = False
    try:
        fork_repo = gh.get_repo(fork_full)
        return fork_repo, created
    except GithubException:
        pass
    try:
        upstream_repo.create_fork()
        created = True
    except GithubException as e:
        raise RuntimeError(f"Failed to create fork for {upstream_repo.full_name}: {e}")

    start = time.time()
    last_err: Optional[Exception] = None
    while time.time() - start < timeout_sec:
        try:
            fork_repo = gh.get_repo(fork_full)
            return fork_repo, created
        except GithubException as e:
            last_err = e
            time.sleep(2)
    raise RuntimeError(f"Fork not visible after {timeout_sec}s for {fork_full}: {last_err}")

# ---------------- Main workflow ---------------- #

def main():
    parser = argparse.ArgumentParser(
        description="Bulk replace org URLs + convert LICENSE blob/raw links to ./LICENSE with auto-fork PR workflow."
    )
    parser.add_argument("--org", default="EutropicAI")
    parser.add_argument("--old", default="TensoRaws")
    parser.add_argument("--new", default="EutropicAI")
    parser.add_argument("--only-public", action="store_true")
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--repos", nargs="*", help="Only process these repository names within the org")
    parser.add_argument("--branch-prefix", default="chore/update-org-urls")
    parser.add_argument("--sleep", type=int, default=2, help="Seconds to sleep between repos")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm", action="store_true", help="Ask confirmation before committing per repo")
    parser.add_argument("--always-fork", action="store_true", help="Always use fork workflow even if push permission exists")
    parser.add_argument("--no-convert-license-links", action="store_true",
                        help="Disable converting absolute LICENSE blob/raw links to relative ./LICENSE")
    args = parser.parse_args()

    convert_license_links_flag = not args.no_convert_license_links

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("Warning: GITHUB_TOKEN not set. Set a PAT for better rate limit & PR creation.",
              file=sys.stderr)

    if not have_git():
        print("Error: git not found in PATH.", file=sys.stderr)
        sys.exit(1)

    gh = Github(auth=Auth.Token(token)) if token else Github()

    # Auth user
    try:
        me = gh.get_user()
        my_login = me.login
    except GithubException as e:
        print(f"Failed to get current user: {e}", file=sys.stderr)
        sys.exit(1)

    # Org
    try:
        org = gh.get_organization(args.org)
    except GithubException as e:
        print(f"Failed to access org '{args.org}': {e}", file=sys.stderr)
        sys.exit(1)

    # Repos
    repos = []
    if args.repos:
        for rname in args.repos:
            full = f"{args.org}/{rname}"
            try:
                repos.append(gh.get_repo(full))
            except GithubException as e:
                print(f"Skip {full}: {e}", file=sys.stderr)
    else:
        try:
            count = 0
            for r in org.get_repos(type="all"):
                if count >= args.limit:
                    break
                if not args.include_archived and r.archived:
                    continue
                if r.fork:
                    continue
                if args.only_public and r.private:
                    continue
                repos.append(r)
                count += 1
        except GithubException as e:
            print(f"Failed listing repos: {e}", file=sys.stderr)
            sys.exit(1)

    print(f"Organization: {args.org}")
    print(f"Replace: {args.old} -> {args.new}")
    print(f"Convert LICENSE links (blob+raw): {convert_license_links_flag}")
    print(f"Dry-run: {args.dry_run}")
    print(f"Repos to process: {len(repos)}")
    print(f"Acting as GitHub user: {my_login}")

    tmp_root = Path(tempfile.mkdtemp(prefix="bulk-update-org-urls-"))
    print(f"Workdir: {tmp_root}")

    processed = 0
    changed = 0

    try:
        for repo in repos:
            processed += 1
            full = repo.full_name
            default_branch = repo.default_branch or "main"
            repo_name = repo.name
            print("-" * 70)
            print(f"[{processed}/{len(repos)}] {full} (default: {default_branch})")

            have_push = can_push_to_repo(repo, my_login)
            use_fork = args.always_fork or not have_push

            repo_dir = tmp_root / repo_name
            if repo_dir.exists():
                shutil.rmtree(repo_dir, ignore_errors=True)

            if not clone_repo(full, repo_dir, token):
                print("  Clone failed, skip.")
                time.sleep(args.sleep)
                continue

            scan_results = scan_repo(repo_dir, args.old, repo_name, convert_license_links_flag)
            if not scan_results:
                print("  No occurrences (old URL / LICENSE blob/raw links) found. Skipping.")
                time.sleep(args.sleep)
                continue

            total_old_hits = sum(v["old_hits"] for v in scan_results.values())
            total_blob_hits = sum(v["license_blob"] for v in scan_results.values())
            total_raw_hits = sum(v["license_raw"] for v in scan_results.values())

            print(f"  Scan summary: files={len(scan_results)}, "
                  f"old_url_hits={total_old_hits}, license_blob_hits={total_blob_hits}, license_raw_hits={total_raw_hits}")

            for fpath, counts in list(scan_results.items())[:20]:
                rel = Path(fpath).relative_to(repo_dir)
                parts = []
                if counts["old_hits"]:
                    parts.append(f"old:{counts['old_hits']}")
                if counts["license_blob"]:
                    parts.append(f"blob:{counts['license_blob']}")
                if counts["license_raw"]:
                    parts.append(f"raw:{counts['license_raw']}")
                print(f"    - {rel} ({', '.join(parts)})")
            if len(scan_results) > 20:
                print(f"    ... and {len(scan_results) - 20} more files")

            if args.dry_run:
                print("  DRY-RUN: not creating branch / committing / pushing / PR.")
                time.sleep(args.sleep)
                continue

            if args.confirm:
                ans = input("  Proceed? [y/N]: ").strip().lower()
                if ans not in ("y", "yes"):
                    print("  Skipped by user.")
                    time.sleep(args.sleep)
                    continue

            branch_tail = (f"from-{args.old.split('://')[-1].replace('/', '-')}-"
                           f"to-{args.new.split('://')[-1].replace('/', '-')}")
            branch_name = sanitize_branch_name(f"{args.branch_prefix}/{branch_tail}")

            try:
                ensure_branch(repo_dir, default_branch, branch_name)
            except Exception as e:
                print(f"  Failed to create/switch branch: {e}")
                time.sleep(args.sleep)
                continue

            per_file, old_total, blob_total, raw_total = apply_replacements(
                repo_dir, args.old, args.new, repo_name, convert_license_links_flag
            )
            if old_total == 0 and blob_total == 0 and raw_total == 0:
                print("  After applying replacements, no diff. Skipping.")
                time.sleep(args.sleep)
                continue

            commit_msg_lines = [
                "chore: update org URLs and normalize LICENSE links",
                "",
                "Changes:",
                f"- Replaced {old_total} occurrence(s) of\n  {args.old}\n  with\n  {args.new}" if old_total else "- (No org URL replacements)",
            ]
            if convert_license_links_flag:
                commit_msg_lines.append(
                    f"- Converted LICENSE links: blob={blob_total}, raw={raw_total}"
                    if (blob_total or raw_total) else "- (No LICENSE link conversions)"
                )
            else:
                commit_msg_lines.append("- LICENSE link conversion disabled")
            commit_msg_lines.extend([
                "",
                "Reason: organization rename + prefer stable relative license links."
            ])
            commit_msg = "\n".join(commit_msg_lines)

            try:
                did_commit = commit_all(repo_dir, commit_msg)
                if not did_commit:
                    print("  Nothing staged to commit. Skipping.")
                    time.sleep(args.sleep)
                    continue
            except Exception as e:
                print(f"  Commit failed: {e}")
                time.sleep(args.sleep)
                continue

            pr_head = branch_name
            push_remote = "origin"

            if use_fork:
                try:
                    fork_repo, created = ensure_fork(gh, repo, my_login)
                    fork_full = fork_repo.full_name
                    fork_url = https_url(fork_full, token)
                    add_or_update_remote(repo_dir, "fork", fork_url)
                    push_remote = "fork"
                    pr_head = f"{fork_repo.owner.login}:{branch_name}"
                    print(f"  Using fork: {fork_full} (created:{created})")
                except Exception as e:
                    print(f"  Could not ensure fork: {e}")
                    time.sleep(args.sleep)
                    continue

            if not push_branch_to_remote(repo_dir, push_remote, branch_name):
                if not use_fork:
                    print("  Push to origin failed. Falling back to fork...")
                    try:
                        fork_repo, created = ensure_fork(gh, repo, my_login)
                        fork_full = fork_repo.full_name
                        fork_url = https_url(fork_full, token)
                        add_or_update_remote(repo_dir, "fork", fork_url)
                        if not push_branch_to_remote(repo_dir, "fork", branch_name):
                            print("  Push to fork failed. Skipping.")
                            time.sleep(args.sleep)
                            continue
                        pr_head = f"{fork_repo.owner.login}:{branch_name}"
                        use_fork = True
                    except Exception as e:
                        print(f"  Fallback fork failed: {e}")
                        time.sleep(args.sleep)
                        continue
                else:
                    print("  Push failed. Skipping.")
                    time.sleep(args.sleep)
                    continue

            pr_title = "chore: replace org URLs and LICENSE links"
            pr_body_lines = [
                "This PR updates explicit org URLs and converts absolute LICENSE links to relative form.",
                "",
                f"Org URL replacements: {old_total}",
                f"LICENSE link conversions (blob): {blob_total}",
                f"LICENSE link conversions (raw): {raw_total}",
                "",
                f"Old base: {args.old}",
                f"New base: {args.new}",
                "",
                "Relative ./LICENSE links stay correct if branches or hosts change."
            ]
            pr_body = "\n".join(pr_body_lines)

            try:
                existing = list(repo.get_pulls(state="open", head=pr_head, base=default_branch))
                if existing:
                    print(f"  PR already exists: {existing[0].html_url}")
                else:
                    pr = repo.create_pull(title=pr_title, body=pr_body, head=pr_head, base=default_branch)
                    print(f"  Created PR: {pr.html_url}")
                changed += 1
            except GithubException as e:
                print(f"  Failed to create PR: {e}")

            time.sleep(args.sleep)

    finally:
        try:
            shutil.rmtree(tmp_root, ignore_errors=True)
        except Exception:
            pass

    print("-" * 70)
    print(f"Done. Repos scanned: {processed}; PRs created/updated: {changed}")


if __name__ == "__main__":
    main()
