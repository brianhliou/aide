#!/usr/bin/env sh
set -u

count_lines() {
  sed '/^$/d' | wc -l | tr -d ' '
}

root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "FAIL codex-status: not inside a Git checkout" >&2
  exit 1
}

branch=$(git branch --show-current 2>/dev/null)
head=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)
status=$(git status --porcelain 2>/dev/null || true)
dirty_count=$(printf '%s\n' "$status" | count_lines)
staged_count=$(printf '%s\n' "$status" | awk 'length($0) && substr($0,1,1) != " " && substr($0,1,1) != "?" { n++ } END { print n + 0 }')
unstaged_count=$(printf '%s\n' "$status" | awk 'length($0) && (substr($0,2,1) != " " || substr($0,1,2) == "??") { n++ } END { print n + 0 }')

echo "Codex coordination status"
echo "- checkout: $root"
echo "- branch: ${branch:-"(detached)"}"
if [ -n "$upstream" ]; then
  echo "- head: $head ($upstream)"
else
  echo "- head: $head"
fi
echo "- dirty paths: $dirty_count staged=$staged_count unstaged=$unstaged_count"
printf '%s\n' "$status" | sed '/^$/d' | sed -n '1,12p' | cut -c4- | sed 's/^/  - /'
if [ "$dirty_count" -gt 12 ]; then
  echo "  - ... $((dirty_count - 12)) more"
fi

echo "- worktrees:"
current_worktree=""
current_head=""
current_branch=""
current_detached=0
flush_worktree() {
  [ -n "$current_worktree" ] || return
  marker=" "
  [ "$current_worktree" = "$root" ] && marker="*"
  label="$current_branch"
  [ "$current_detached" -eq 1 ] && label="(detached)"
  [ -n "$label" ] || label="(unknown)"
  short_head=$(printf '%s' "$current_head" | cut -c1-7)
  [ -n "$short_head" ] || short_head="unknown"
  echo "  $marker $label $short_head $current_worktree"
}
git worktree list --porcelain 2>/dev/null | while IFS= read -r line; do
  if [ -z "$line" ]; then
    flush_worktree
    current_worktree=""
    current_head=""
    current_branch=""
    current_detached=0
    continue
  fi
  key=${line%% *}
  value=${line#* }
  case "$key" in
    worktree)
      flush_worktree
      current_worktree=$value
      current_head=""
      current_branch=""
      current_detached=0
      ;;
    HEAD)
      current_head=$value
      ;;
    branch)
      current_branch=${value#refs/heads/}
      ;;
    detached)
      current_detached=1
      ;;
  esac
done

if processes=$(ps -axo comm= 2>/dev/null); then
  summary=$(printf '%s\n' "$processes" | awk -F/ '
    {
      name=$NF
      if (name == "codex") codex++
      else if (name == "next" || name == "next-server") next++
      else if (name == "node") node++
      else if (name == "npm") npm++
      else if (name == "make") make++
      else if (name == "python" || name == "python3") python++
      else if (name == "uv") uv++
    }
    END {
      out=""
      if (codex) out=out " codex=" codex
      if (next) out=out " next=" next
      if (node) out=out " node=" node
      if (npm) out=out " npm=" npm
      if (make) out=out " make=" make
      if (python) out=out " python=" python
      if (uv) out=out " uv=" uv
      sub(/^ /, "", out)
      print out
    }')
  if [ -n "$summary" ]; then
    echo "- active process counts: $summary"
  else
    echo "- active process counts: none matched"
  fi
else
  echo "- active process counts: unavailable"
fi

warnings=""
[ "$branch" = "main" ] && warnings="${warnings}
  - current checkout is on main; use this for integration, not parallel implementation"
[ "$dirty_count" -gt 0 ] && warnings="${warnings}
  - working tree has $dirty_count changed path(s)"

if [ -n "$warnings" ]; then
  echo "- warnings:"
  printf '%s\n' "$warnings" | sed '/^$/d'
else
  echo "- warnings: none"
fi
