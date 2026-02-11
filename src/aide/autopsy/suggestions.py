"""Rules engine for CLAUDE.md improvement suggestions.

Each rule inspects session data and produces zero or more suggestions.
All rules are heuristic-based — no LLM calls.
"""

from __future__ import annotations

from aide.autopsy.analyzer import CacheEfficiency, ClaudeMdSuggestion


def generate_suggestions(
    files_touched: list[dict],
    cache_efficiency: CacheEfficiency,
    compaction_count: int,
    tool_call_count: int,
) -> list[ClaudeMdSuggestion]:
    """Run all suggestion rules and return combined results."""
    suggestions: list[ClaudeMdSuggestion] = []

    # Rule 1: repeated_reads — files read 3+ times, capped at top 10
    repeated = [f for f in files_touched if f["read_count"] >= 3]
    repeated.sort(key=lambda f: f["read_count"], reverse=True)
    for f in repeated[:10]:
        suggestions.append(
            ClaudeMdSuggestion(
                category="repeated_reads",
                priority="high",
                suggestion=(
                    f"File `{f['file_path']}` was read {f['read_count']} times "
                    f"this session. Consider adding its key types/exports to CLAUDE.md."
                ),
                evidence=f"Read {f['read_count']}x, edited {f['edit_count']}x",
            )
        )

    # Rule 2: low cache hit rate (< 50%)
    if cache_efficiency.cache_hit_rate < 0.50:
        suggestions.append(
            ClaudeMdSuggestion(
                category="cache_efficiency",
                priority="medium",
                suggestion=(
                    "Cache hit rate was {:.0%}. Adding more project context "
                    "to CLAUDE.md can improve cache reuse.".format(
                        cache_efficiency.cache_hit_rate
                    )
                ),
                evidence=(
                    f"Cache reads: {cache_efficiency.cache_read_tokens:,} / "
                    f"Total: "
                    f"{cache_efficiency.total_input_tokens + cache_efficiency.cache_read_tokens:,}"
                ),
            )
        )

    # Rule 3: high compaction count (2+)
    if compaction_count >= 2:
        suggestions.append(
            ClaudeMdSuggestion(
                category="session_structure",
                priority="medium",
                suggestion=(
                    f"Context was compacted {compaction_count} times. Consider "
                    f"breaking complex tasks into smaller, focused sessions."
                ),
                evidence=f"{compaction_count} compaction events detected",
            )
        )

    # Rule 4: many tool calls (50+)
    if tool_call_count >= 50:
        suggestions.append(
            ClaudeMdSuggestion(
                category="efficiency",
                priority="low",
                suggestion=(
                    f"Session made {tool_call_count} tool calls. If many are "
                    f"exploratory reads, adding a project map to CLAUDE.md can "
                    f"reduce unnecessary file exploration."
                ),
                evidence=f"{tool_call_count} total tool calls",
            )
        )

    return suggestions
