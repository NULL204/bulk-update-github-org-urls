#!/usr/bin/env bash
set -euo pipefail

# =========================
# Config (env overridable)
# =========================
ORG="${ORG:-EutropicAI}"
OLD="${OLD:-https://github.com/TensoRaws}"
NEW="${NEW:-https://github.com/EutropicAI}"
BRANCH_PREFIX="${BRANCH_PREFIX:-chore/update-org-urls}"
LIMIT="${LIMIT:-1000}"                 # Max repos to process
DRY_RUN="${DRY_RUN:-false}"            # true/false: true仅显示将要修改的文件，不创建PR
ONLY_PUBLIC="${ONLY_PUBLIC:-false}"    # true只处理公开仓库
INCLUDE_ARCHIVED="${INCLUDE_ARCHIVED:-false}" # true包含归档仓库
SLEEP_BETWEEN_REPOS="${SLEEP_BETWEEN_REPOS:-2}" # 防止速率限制，单位秒

# 可选：只处理某些仓库（空则全量）。示例：TARGET_REPOS="repo1 repo2 repo3"
TARGET_REPOS="${TARGET_REPOS:-}"

# =========================
# Dependency checks
# =========================
need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required tool: $1"; exit 1; }
}
need gh
need jq
need git
need perl

# 优先用 ripgrep 搜索，若无则用 git grep 兜底
HAS_RG="false"
if command -v rg >/dev/null 2>&1; then
  HAS_RG="true"
fi

# =========================
# Helpers
# =========================
log() { printf '%s\n' "$*" >&2; }
hr() { printf '%*s\n' 60 '' | tr ' ' '-'; }

list_repos() {
  local jq_filter
  if [ "$INCLUDE_ARCHIVED" = "true" ]; then
    jq_filter='.[] | select(.isFork==false and (.viewerPermission=="WRITE" or .viewerPermission=="ADMIN"))'
  else
    jq_filter='.[] | select(.isArchived==false and .isFork==false and (.viewerPermission=="WRITE" or .viewerPermission=="ADMIN"))'
  fi
  if [ "$ONLY_PUBLIC" = "true" ]; then
    jq_filter="$jq_filter | select(.visibility==\"PUBLIC\")"
  fi
  jq_filter="$jq_filter | [.name, .defaultBranchRef.name] | @tsv"

  gh repo list "$ORG" --limit "$LIMIT" --json name,isArchived,isFork,defaultBranchRef,visibility,viewerPermission -q "$jq_filter"
}

find_files_with_old_url() {
  if [ "$HAS_RG" = "true" ]; then
    # -I 忽略二进制; -l 仅文件名; -0 空字符分割; --hidden 包含隐藏; --no-ignore-vcs 忽略.gitignore
    # 过滤若干常见二进制/产物目录
    rg -Il -0 --hidden --no-ignore-vcs \
      --glob '!.git' \
      --glob '!node_modules' \
      --glob '!vendor' \
      --glob '!dist' \
      --glob '!build' \
      --glob '!*.png' --glob '!*.jpg' --glob '!*.jpeg' --glob '!*.gif' \
      --glob '!*.pdf' --glob '!*.zip' --glob '!*.tar*' --glob '!*.gz' --glob '!*.7z' \
      --glob '!*.woff*' --glob '!*.ttf' --glob '!*.ico' --glob '!*.mp4' \
      -- "$OLD" 2>/dev/null || true
  else
    # git grep 兜底：-I 忽略二进制, -l 列文件, -z 空分隔
    git grep -Ilz -e "$OLD" -- . ':(exclude).git' ':(exclude)node_modules' ':(exclude)vendor' \
      ':(exclude)dist' ':(exclude)build' \
      ':(exclude)*.png' ':(exclude)*.jpg' ':(exclude)*.jpeg' ':(exclude)*.gif' \
      ':(exclude)*.pdf' ':(exclude)*.zip' ':(exclude)*.tar*' ':(exclude)*.gz' ':(exclude)*.7z' \
      ':(exclude)*.woff*' ':(exclude)*.ttf' ':(exclude)*.ico' ':(exclude)*.mp4' 2>/dev/null || true
  fi
}

replace_in_file() {
  # 用 perl，避免 sed 在不同平台(BSD/GNU)的差异和分隔符转义问题
  OLD_URL="$OLD" NEW_URL="$NEW" perl -0777 -i -pe 'BEGIN{$old=$ENV{OLD_URL}; $new=$ENV{NEW_URL}} s/\Q$old\E/$new/g' "$1"
}

create_branch_if_needed() {
  local default_branch="$1"
  local branch_name="$2"
  git checkout -q "$default_branch"
  if git rev-parse --verify -q "$branch_name" >/dev/null; then
    git checkout -q "$branch_name"
  else
    git checkout -qb "$branch_name" "origin/$default_branch"
  fi
}

# =========================
# Main
# =========================
log "Organization: $ORG"
log "Replace: $OLD  ->  $NEW"
log "DRY_RUN: $DRY_RUN"
log "ONLY_PUBLIC: $ONLY_PUBLIC"
log "INCLUDE_ARCHIVED: $INCLUDE_ARCHIVED"
log "Repos limit: $LIMIT"
[ -n "$TARGET_REPOS" ] && log "Target repos: $TARGET_REPOS"
hr

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

declare -a REPO_LINES
if [ -n "$TARGET_REPOS" ]; then
  # 通过 gh 获取这些仓库各自默认分支名
  while read -r R; do
    [ -z "$R" ] && continue
    DBR="$(gh repo view "$ORG/$R" --json defaultBranchRef -q '.defaultBranchRef.name')"
    echo -e "$R\t$DBR"
  done <<<"$TARGET_REPOS" >"$WORKDIR/repos.tsv"
else
  list_repos >"$WORKDIR/repos.tsv"
fi

TOTAL=0
CHANGED=0

while IFS=$'\t' read -r NAME DEFAULT_BRANCH; do
  [ -z "$NAME" ] && continue
  TOTAL=$((TOTAL+1))
  BRANCH="$BRANCH_PREFIX/$OLD-to-$NEW"           # 分支名可包含斜杠
  SAFE_BRANCH="${BRANCH//[^a-zA-Z0-9._\/-]/-}"   # 简单清洗
  REPO_DIR="$WORKDIR/$NAME"

  log ""
  hr
  log "[$TOTAL] Processing $ORG/$NAME (default: $DEFAULT_BRANCH)"
  gh repo clone "$ORG/$NAME" "$REPO_DIR" -- --quiet --depth 1 || { log "Clone failed, skip."; continue; }

  pushd "$REPO_DIR" >/dev/null

  # 找出命中文件
  if [ "$HAS_RG" = "true" ]; then
    mapfile -d '' FILES < <(find_files_with_old_url)
  else
    # git grep 输出以NUL分割
    IFS='' read -r -d '' -a FILES < <(find_files_with_old_url && printf '\0')
  fi

  if [ "${#FILES[@]}" -eq 0 ]; then
    log "No occurrences. Skipping."
    popd >/dev/null
    sleep "$SLEEP_BETWEEN_REPOS"
    continue
  fi

  log "Found ${#FILES[@]} file(s) containing OLD url:"
  for f in "${FILES[@]}"; do
    printf ' - %s\n' "$f"
  done

  if [ "$DRY_RUN" = "true" ]; then
    log "DRY_RUN=true, not changing files."
    popd >/dev/null
    sleep "$SLEEP_BETWEEN_REPOS"
    continue
  fi

  create_branch_if_needed "$DEFAULT_BRANCH" "$SAFE_BRANCH"

  # 执行替换
  for f in "${FILES[@]}"; do
    replace_in_file "$f"
  done

  if git diff --quiet; then
    log "After replacement, no diff. Skipping commit/PR."
    popd >/dev/null
    sleep "$SLEEP_BETWEEN_REPOS"
    continue
  fi

  git add -A
  COMMIT_MSG="chore: update org URLs from TensoRaws to EutropicAI

This updates occurrences of:
$OLD
to:
$NEW

Reason: the organization was renamed, updating docs/links for clarity."
  git commit -m "$COMMIT_MSG" >/dev/null
  git push -u origin "$SAFE_BRANCH" >/dev/null

  # 创建 PR
  PR_TITLE="chore: replace TensoRaws org URLs with EutropicAI"
  PR_BODY="We renamed the organization. This PR updates occurrences of:
- $OLD
to:
- $NEW

Notes:
- Only text files were modified.
- Binary/assets and common build directories were skipped.
"

  if gh pr view --repo "$ORG/$NAME" "$SAFE_BRANCH" >/dev/null 2>&1; then
    log "PR already exists for branch $SAFE_BRANCH"
  else
    PR_URL="$(gh pr create --base "$DEFAULT_BRANCH" --head "$SAFE_BRANCH" --title "$PR_TITLE" --body "$PR_BODY")"
    log "Created PR: $PR_URL"
  fi

  CHANGED=$((CHANGED+1))
  popd >/dev/null
  sleep "$SLEEP_BETWEEN_REPOS"
done <"$WORKDIR/repos.tsv"

hr
log "Done. Repos scanned: $TOTAL; PRs created/updated: $CHANGED"