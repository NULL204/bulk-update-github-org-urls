#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import os
import sys
import tempfile
import time
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple, Optional

from github import Github, GithubException, Auth

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

def run(cmd: List[str], cwd: Optional[Path] = None, check: bool = True, capture_output: bool = False, text: bool = True):
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check, capture_output=capture_output, text=text)

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
    try:
        s = p.read_text(encoding="utf-8")
        return s, "utf-8"
    except Exception:
        pass
    try:
        s = p.read_text(encoding="latin-1")
        return s, "latin-1"
    except Exception:
        return None, None

def scan_repo_for_old(repo_dir: Path, old: str) -> Tuple[List[Tuple[Path, int]], int]:
    hits: List[Tuple[Path, int]] = []
    total = 0
    old_bytes = old.encode("utf-8", errors="ignore")

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
                if old_bytes not in data:
                    continue
            except Exception:
                continue

            text, _enc = read_text_with_fallback(path)
            if text is None:
                continue
            count = text.count(old)
            if count > 0:
                hits.append((path, count))
                total += count

    return hits, total

def replace_in_repo(repo_dir: Path, old: str, new: str) -> Tuple[List[Tuple[Path, int]], int]:
    hits: List[Tuple[Path, int]] = []
    total = 0
    old_bytes = old.encode("utf-8", errors="ignore")

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
                if old_bytes not in data:
                    continue
            except Exception:
                continue

            text, enc = read_text_with_fallback(path)
            if text is None:
                continue

            count = text.count(old)
            if count <= 0:
                continue

            new_text = text.replace(old, new)
            try:
                path.write_text(new_text, encoding=enc or "utf-8")
                hits.append((path, count))
                total += count
            except Exception:
                continue

    return hits, total

def ensure_branch(cwd: Path, default_branch: str, branch: str):
    run(["git", "checkout", default_branch], cwd=cwd)
    try:
        run(["git", "fetch", "origin", default_branch], cwd=cwd, check=False)
    except Exception:
        pass
    res = run(["git", "rev-parse", "--verify", branch], cwd=cwd, check=False, capture_output=True)
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
    # Add new remote or set-url if exists
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
    # If you own it, you can push
    try:
        return repo.owner.login == my_login if my_login else False
    except Exception:
        return False

def ensure_fork(gh, upstream_repo, my_login: str, timeout_sec: int = 180):
    """
    确保在 my_login 名下存在 upstream_repo 的 fork。优先用 API，
    若 403（无权限）则回退到 gh repo fork。返回 (fork_repo, created_bool)。
    """
    from github import GithubException
    import time
    import subprocess

    fork_full = f"{my_login}/{upstream_repo.name}"

    # 1) 已存在的 fork
    try:
        fork_repo = gh.get_repo(fork_full)
        return fork_repo, False
    except GithubException:
        pass

    # 2) API 尝试创建 fork
    try:
        upstream_repo.create_fork()
        created = True
    except GithubException as e:
        # 403 或其他错误，回退到 gh
        created = False
        try:
            subprocess.run(
                ["gh", "repo", "fork", upstream_repo.full_name, "--clone=false", "--remote=false"],
                check=True,
                capture_output=True,
                text=True,
            )
            created = True
        except Exception as gh_e:
            raise RuntimeError(f"Failed to create fork via API ({e}) and gh ({gh_e}).")

    # 3) 等待 fork 生效
    start = time.time()
    last_err = None
    while time.time() - start < timeout_sec:
        try:
            fork_repo = gh.get_repo(fork_full)
            return fork_repo, created
        except GithubException as e:
            last_err = e
            time.sleep(2)

    raise RuntimeError(f"Fork not visible after {timeout_sec}s for {fork_full}: {last_err}")

def main():
    parser = argparse.ArgumentParser(description="Bulk replace GitHub org URLs across repositories and open PRs with auto-fork fallback (Windows-friendly).")
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
    parser.add_argument("--confirm", action="store_true", help="Ask for confirmation before committing per repo")
    parser.add_argument("--always-fork", action="store_true", help="Always work via fork even if you have push permission")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("Warning: GITHUB_TOKEN not set. Please set it to a PAT with repo and PR permissions.", file=sys.stderr)

    if not have_git():
        print("Error: git not found on PATH. Please install Git for Windows and retry.", file=sys.stderr)
        sys.exit(1)

    gh = Github(auth=Auth.Token(token)) if token else Github()

    # Who am I
    try:
        me = gh.get_user()
        my_login = me.login
    except GithubException as e:
        print(f"Failed to get current user: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        org = gh.get_organization(args.org)
    except GithubException as e:
        print(f"Failed to access org '{args.org}': {e}", file=sys.stderr)
        sys.exit(1)

    # Collect repos
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
            print("-" * 70)
            print(f"[{processed}/{len(repos)}] {full} (default: {default_branch})")

            # Decide whether to fork
            have_push = can_push_to_repo(repo, my_login)
            use_fork = args.always_fork or not have_push

            repo_dir = tmp_root / repo.name
            if repo_dir.exists():
                shutil.rmtree(repo_dir, ignore_errors=True)

            # Clone upstream
            if not clone_repo(full, repo_dir, token):
                print("  Clone failed, skip.")
                time.sleep(args.sleep)
                continue

            # Scan first
            hits_preview, total_preview = scan_repo_for_old(repo_dir, args.old)
            if total_preview == 0:
                print("  No occurrences found. Skipping.")
                time.sleep(args.sleep)
                continue

            print(f"  Found {total_preview} occurrence(s) in {len(hits_preview)} file(s):")
            for p, c in hits_preview[:20]:
                print(f"    - {p.relative_to(repo_dir)} ({c})")
            if len(hits_preview) > 20:
                print(f"    ... and {len(hits_preview) - 20} more files")

            if args.dry_run:
                print("  DRY-RUN: not changing branch/committing/PR.")
                time.sleep(args.sleep)
                continue

            if args.confirm:
                ans = input("  Proceed to commit and open PR? [y/N]: ").strip().lower()
                if ans not in ("y", "yes"):
                    print("  Skipped by user.")
                    time.sleep(args.sleep)
                    continue

            # Create working branch from upstream default
            branch_tail = f"from-{args.old.split('://')[-1].replace('/', '-')}-to-{args.new.split('://')[-1].replace('/', '-')}"
            branch_name = sanitize_branch_name(f"{args.branch_prefix}/{branch_tail}")

            try:
                ensure_branch(repo_dir, default_branch, branch_name)
            except Exception as e:
                print(f"  Failed to create/switch branch: {e}")
                time.sleep(args.sleep)
                continue

            # Do replacements on branch
            hits, total = replace_in_repo(repo_dir, args.old, args.new)
            if total == 0:
                print("  After branch switch, found no changes. Skipping.")
                time.sleep(args.sleep)
                continue

            commit_msg = f"""chore: update org URLs from TensoRaws to EutropicAI

This updates occurrences of:
{args.old}
to:
{args.new}

Reason: the organization was renamed; updating docs/links for clarity.
"""
            try:
                did_commit = commit_all(repo_dir, commit_msg)
                if not did_commit:
                    print("  Nothing to commit after changes. Skipping.")
                    time.sleep(args.sleep)
                    continue
            except Exception as e:
                print(f"  Commit failed: {e}")
                time.sleep(args.sleep)
                continue

            # Prepare push target
            pr_head = branch_name
            push_remote = "origin"

            if use_fork:
                try:
                    fork_repo, created = ensure_fork(gh, repo, my_login)
                    fork_full = fork_repo.full_name  # e.g., NULL204/Repo
                    fork_url = https_url(fork_full, token)
                    add_or_update_remote(repo_dir, "fork", fork_url)
                    push_remote = "fork"
                    pr_head = f"{fork_repo.owner.login}:{branch_name}"
                    print(f"  Using fork: {fork_full} (created: {created})")
                except Exception as e:
                    print(f"  Could not ensure fork: {e}")
                    time.sleep(args.sleep)
                    continue

            # Push
            if not push_branch_to_remote(repo_dir, push_remote, branch_name):
                # If origin push failed and we weren't using fork yet, fallback to fork automatically
                if not use_fork:
                    print("  Push to origin failed. Falling back to fork workflow...")
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
                        print(f"  Fallback to fork failed: {e}")
                        time.sleep(args.sleep)
                        continue
                else:
                    print("  Push failed. Skipping.")
                    time.sleep(args.sleep)
                    continue

            # Create PR on upstream repo
            pr_title = "chore: replace TensoRaws org URLs with EutropicAI"
            pr_body = f"""We renamed the organization. This PR updates occurrences of:
- {args.old}
to:
- {args.new}

Notes:
- Only text-like files were modified; common binary/build paths skipped.
- Please review and merge when ready.
"""
            try:
                # Avoid duplicate PRs
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