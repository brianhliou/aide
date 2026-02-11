"""Core analysis functions for session autopsy.

All functions are pure — they take pre-fetched data (dicts from DB queries)
and return structured dataclasses. No database access here.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from aide.cost import estimate_cost

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ToolCount:
    tool_name: str
    count: int


@dataclass
class FileAccessCount:
    file_path: str
    read_count: int
    edit_count: int
    write_count: int
    total: int


@dataclass
class SessionSummary:
    session_id: str
    project_name: str
    started_at: str
    ended_at: str | None
    duration_seconds: int | None
    message_count: int
    user_message_count: int
    assistant_message_count: int
    tool_call_count: int
    tool_breakdown: list[ToolCount]
    files_modified: list[str]  # unique file_paths from Write/Edit
    files_read: list[str]  # unique file_paths from Read/Glob/Grep
    estimated_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    total_cache_creation_tokens: int


@dataclass
class CostCategory:
    category: str  # file_reads, code_generation, execution, orchestration, system_overhead
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    estimated_cost_usd: float
    percentage: float


@dataclass
class ExpensiveTurn:
    turn_number: int
    role: str
    tool_names: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    estimated_cost_usd: float


@dataclass
class CacheEfficiency:
    total_input_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    fresh_input_tokens: int
    cache_hit_rate: float
    cache_savings_usd: float


@dataclass
class CostAnalysis:
    total_cost_usd: float
    categories: list[CostCategory]
    most_expensive_turns: list[ExpensiveTurn]
    cache_efficiency: CacheEfficiency


@dataclass
class ContextPoint:
    turn_number: int
    cumulative_input_tokens: int
    role: str


@dataclass
class CompactionEvent:
    turn_number: int
    tokens_before: int
    tokens_after: int
    estimated_tokens_lost: int


@dataclass
class ContextAnalysis:
    context_curve: list[ContextPoint]
    peak_context_tokens: int
    estimated_compaction_count: int
    compaction_events: list[CompactionEvent]
    context_utilization_pct: float  # peak / 200K
    avg_input_tokens_per_turn: int


@dataclass
class ClaudeMdSuggestion:
    category: str
    priority: str  # "high", "medium", "low"
    suggestion: str
    evidence: str


@dataclass
class SuggestionsReport:
    suggestions: list[ClaudeMdSuggestion]
    top_accessed_files: list[FileAccessCount]
    repeated_read_files: list[FileAccessCount]  # read 3+ times


# ---------------------------------------------------------------------------
# Tool → category mapping
# ---------------------------------------------------------------------------

TOOL_CATEGORIES: dict[str, str] = {
    "Read": "file_reads",
    "Glob": "file_reads",
    "Grep": "file_reads",
    "Write": "code_generation",
    "Edit": "code_generation",
    "Bash": "execution",
    "Task": "orchestration",
    "TaskCreate": "orchestration",
    "TaskUpdate": "orchestration",
    "TaskList": "orchestration",
    "SendMessage": "orchestration",
    "WebFetch": "orchestration",
    "WebSearch": "orchestration",
}

CONTEXT_WINDOW = 200_000


# ---------------------------------------------------------------------------
# Analyzer functions
# ---------------------------------------------------------------------------


def analyze_summary(
    session: dict,
    tool_usage: list[dict],
    files_touched: list[dict],
) -> SessionSummary:
    """Section 1: factual session overview.

    Extracts from session dict, builds tool_breakdown from tool_usage,
    splits files_touched into files_modified and files_read.
    """
    tool_breakdown = [
        ToolCount(tool_name=t["tool_name"], count=t["count"])
        for t in tool_usage
    ]

    files_modified = sorted(
        {
            f["file_path"]
            for f in files_touched
            if f["edit_count"] > 0 or f["write_count"] > 0
        }
    )
    files_read = sorted(
        {f["file_path"] for f in files_touched if f["read_count"] > 0}
    )

    return SessionSummary(
        session_id=session["session_id"],
        project_name=session["project_name"],
        started_at=session["started_at"],
        ended_at=session.get("ended_at"),
        duration_seconds=session.get("duration_seconds"),
        message_count=session["message_count"],
        user_message_count=session["user_message_count"],
        assistant_message_count=session["assistant_message_count"],
        tool_call_count=session["tool_call_count"],
        tool_breakdown=tool_breakdown,
        files_modified=files_modified,
        files_read=files_read,
        estimated_cost_usd=session["estimated_cost_usd"],
        total_input_tokens=session["total_input_tokens"],
        total_output_tokens=session["total_output_tokens"],
        total_cache_read_tokens=session["total_cache_read_tokens"],
        total_cache_creation_tokens=session["total_cache_creation_tokens"],
    )


def _categorize_message(msg: dict) -> str:
    """Determine cost category for a single message."""
    if msg["role"] != "assistant":
        return "system_overhead"

    tool_names_str = msg.get("tool_names")
    if not tool_names_str:
        return "system_overhead"

    tools = [t.strip() for t in tool_names_str.split(",")]
    for tool in tools:
        category = TOOL_CATEGORIES.get(tool)
        if category:
            return category

    return "orchestration"


def analyze_cost(
    session: dict,
    messages: list[dict],
    tool_calls: list[dict],
) -> CostAnalysis:
    """Section 2: cost breakdown by category.

    Categorizes each message based on its tool_names field, aggregates tokens
    per category, computes cost per category, finds top 5 expensive turns,
    and calculates cache efficiency.
    """
    # Accumulate tokens per category
    cat_tokens: dict[str, dict[str, int]] = {}
    all_categories = (
        "file_reads", "code_generation", "execution",
        "orchestration", "system_overhead",
    )
    for cat_name in all_categories:
        cat_tokens[cat_name] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }

    # Per-turn cost tracking for expensive turns
    turn_costs: list[ExpensiveTurn] = []

    for i, msg in enumerate(messages):
        category = _categorize_message(msg)
        cat_tokens[category]["input_tokens"] += msg.get("input_tokens", 0) or 0
        cat_tokens[category]["output_tokens"] += msg.get("output_tokens", 0) or 0
        cat_tokens[category]["cache_read_tokens"] += msg.get("cache_read_tokens", 0) or 0
        cat_tokens[category]["cache_creation_tokens"] += msg.get("cache_creation_tokens", 0) or 0

        turn_cost = estimate_cost(
            msg.get("input_tokens", 0) or 0,
            msg.get("output_tokens", 0) or 0,
            msg.get("cache_read_tokens", 0) or 0,
            msg.get("cache_creation_tokens", 0) or 0,
        )
        turn_costs.append(
            ExpensiveTurn(
                turn_number=i + 1,
                role=msg["role"],
                tool_names=msg.get("tool_names"),
                input_tokens=msg.get("input_tokens", 0) or 0,
                output_tokens=msg.get("output_tokens", 0) or 0,
                cache_read_tokens=msg.get("cache_read_tokens", 0) or 0,
                cache_creation_tokens=msg.get("cache_creation_tokens", 0) or 0,
                estimated_cost_usd=turn_cost,
            )
        )

    # Compute total cost from session (authoritative)
    total_cost = session["estimated_cost_usd"]

    # Build CostCategory list, then compute percentages from category sum
    categories: list[CostCategory] = []
    for cat_name, tokens in cat_tokens.items():
        cat_cost = estimate_cost(
            tokens["input_tokens"],
            tokens["output_tokens"],
            tokens["cache_read_tokens"],
            tokens["cache_creation_tokens"],
        )
        categories.append(
            CostCategory(
                category=cat_name,
                input_tokens=tokens["input_tokens"],
                output_tokens=tokens["output_tokens"],
                cache_read_tokens=tokens["cache_read_tokens"],
                cache_creation_tokens=tokens["cache_creation_tokens"],
                estimated_cost_usd=cat_cost,
                percentage=0.0,  # will be set below
            )
        )

    # Compute percentages from the sum of category costs so they sum to ~100%
    category_total = sum(c.estimated_cost_usd for c in categories)
    for cat in categories:
        if category_total > 0:
            cat.percentage = round(
                cat.estimated_cost_usd / category_total * 100, 1
            )
        else:
            cat.percentage = 0.0

    # Sort categories by cost descending
    categories.sort(key=lambda c: c.estimated_cost_usd, reverse=True)

    # Top 5 most expensive turns
    most_expensive = sorted(turn_costs, key=lambda t: t.estimated_cost_usd, reverse=True)[:5]

    # Cache efficiency
    fresh_input = session["total_input_tokens"]
    cache_read = session["total_cache_read_tokens"]
    cache_creation = session["total_cache_creation_tokens"]
    denominator = cache_read + fresh_input + cache_creation
    cache_hit_rate = cache_read / denominator if denominator > 0 else 0.0
    cache_savings = cache_read * (3.00 - 0.30) / 1_000_000

    cache_efficiency = CacheEfficiency(
        total_input_tokens=fresh_input,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        fresh_input_tokens=fresh_input,
        cache_hit_rate=cache_hit_rate,
        cache_savings_usd=round(cache_savings, 4),
    )

    return CostAnalysis(
        total_cost_usd=total_cost,
        categories=categories,
        most_expensive_turns=most_expensive,
        cache_efficiency=cache_efficiency,
    )


def analyze_context(messages: list[dict]) -> ContextAnalysis:
    """Section 3: context pressure analysis.

    Filters to assistant messages, builds a context curve from input_tokens,
    detects compaction events (>50% drop from previous when previous >100K),
    computes peak, utilization %, and average.
    """
    # Filter to assistant messages with meaningful input_tokens
    assistant_msgs = [
        m for m in messages if m["role"] == "assistant"
    ]

    if not assistant_msgs:
        return ContextAnalysis(
            context_curve=[],
            peak_context_tokens=0,
            estimated_compaction_count=0,
            compaction_events=[],
            context_utilization_pct=0.0,
            avg_input_tokens_per_turn=0,
        )

    # Build context curve — total context = input + cache_read + cache_creation
    context_curve: list[ContextPoint] = []
    for i, msg in enumerate(assistant_msgs):
        tokens = (
            (msg.get("input_tokens", 0) or 0)
            + (msg.get("cache_read_tokens", 0) or 0)
            + (msg.get("cache_creation_tokens", 0) or 0)
        )
        context_curve.append(
            ContextPoint(
                turn_number=i + 1,
                cumulative_input_tokens=tokens,
                role=msg["role"],
            )
        )

    # Detect compaction events
    compaction_events: list[CompactionEvent] = []
    for i in range(1, len(context_curve)):
        prev_tokens = context_curve[i - 1].cumulative_input_tokens
        curr_tokens = context_curve[i].cumulative_input_tokens
        if prev_tokens > 100_000 and curr_tokens < prev_tokens * 0.5:
            compaction_events.append(
                CompactionEvent(
                    turn_number=context_curve[i].turn_number,
                    tokens_before=prev_tokens,
                    tokens_after=curr_tokens,
                    estimated_tokens_lost=prev_tokens - curr_tokens,
                )
            )

    # Stats
    input_values = [p.cumulative_input_tokens for p in context_curve]
    peak = max(input_values)
    utilization = peak / CONTEXT_WINDOW
    avg = int(mean(input_values))

    return ContextAnalysis(
        context_curve=context_curve,
        peak_context_tokens=peak,
        estimated_compaction_count=len(compaction_events),
        compaction_events=compaction_events,
        context_utilization_pct=utilization,
        avg_input_tokens_per_turn=avg,
    )


def analyze_suggestions(
    files_touched: list[dict],
    cache_efficiency: CacheEfficiency,
    compaction_count: int,
    tool_call_count: int,
) -> SuggestionsReport:
    """Section 4: CLAUDE.md suggestions.

    Delegates to the suggestions rules engine.
    """
    from aide.autopsy.suggestions import generate_suggestions

    suggestions = generate_suggestions(
        files_touched, cache_efficiency, compaction_count, tool_call_count
    )

    # Build FileAccessCount list for top accessed and repeated reads
    file_access_counts = [
        FileAccessCount(
            file_path=f["file_path"],
            read_count=f["read_count"],
            edit_count=f["edit_count"],
            write_count=f["write_count"],
            total=f["total"],
        )
        for f in files_touched
    ]

    top_accessed = file_access_counts[:5]
    repeated_reads = [f for f in file_access_counts if f.read_count >= 3]

    return SuggestionsReport(
        suggestions=suggestions,
        top_accessed_files=top_accessed,
        repeated_read_files=repeated_reads,
    )
