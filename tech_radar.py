#!/usr/bin/env python3
"""
tech_radar.py — AI Agent / MCP / CLI / Automation engineering intelligence radar v3.0

Modes:
  default        Fetch unseen high-signal items, mark delivered items as seen, print English report.
  --show        Preview unseen high-signal items, write a pending batch, do NOT mark items as seen.
  --commit      Mark the last pending --show batch as seen, then clear pending.
  --json        Print structured JSON for agent translation.

Recommended agent flow:
  1) python tech_radar.py --show --json
  2) translate cached JSON output
  3) python tech_radar.py --commit
  4) return translated report
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# ── Config ──────────────────────────────────────────────────────────────────

APP_NAME = "TechRadar/3.0"


def default_data_dir() -> Path:
    """Return a per-user state directory without coupling to any agent runtime."""
    if os.getenv("TECH_RADAR_DATA_DIR"):
        return Path(os.environ["TECH_RADAR_DATA_DIR"]).expanduser()
    if os.name == "nt" and os.getenv("APPDATA"):
        return Path(os.environ["APPDATA"]) / "tech-radar"
    return Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "tech-radar"


DATA_DIR = default_data_dir()
STATE_FILE = Path(os.getenv("TECH_RADAR_STATE", str(DATA_DIR / "state.json"))).expanduser()
PENDING_FILE = Path(os.getenv("TECH_RADAR_PENDING", str(DATA_DIR / "pending.json"))).expanduser()
ENV_FILE = os.getenv("TECH_RADAR_ENV_FILE", "")

# 0–100 多维评分阈值
PASS_THRESHOLD = int(os.getenv("TECH_RADAR_PASS_THRESHOLD", "60"))
CANDIDATE_THRESHOLD = int(os.getenv("TECH_RADAR_CANDIDATE_THRESHOLD", "52"))
MAX_PER_TICK = int(os.getenv("TECH_RADAR_MAX_PER_TICK", "5"))
SEEN_RETENTION = int(os.getenv("TECH_RADAR_SEEN_RETENTION", "500"))

GITHUB_PER_PAGE = int(os.getenv("TECH_RADAR_GITHUB_PER_PAGE", "8"))
HN_PER_PAGE = int(os.getenv("TECH_RADAR_HN_PER_PAGE", "8"))

NEW_REPO_MIN_STARS = int(os.getenv("TECH_RADAR_NEW_REPO_MIN_STARS", "1"))
TRENDING_MIN_STARS = int(os.getenv("TECH_RADAR_TRENDING_MIN_STARS", "20"))
TRENDING_SINCE_DAYS = int(os.getenv("TECH_RADAR_TRENDING_SINCE_DAYS", "7"))

COLLECTION_MIN_STARS = int(os.getenv("TECH_RADAR_COLLECTION_MIN_STARS", "30"))
COLLECTION_SINCE_DAYS = int(os.getenv("TECH_RADAR_COLLECTION_SINCE_DAYS", "14"))

HN_MIN_POINTS = int(os.getenv("TECH_RADAR_HN_MIN_POINTS", "2"))
HN_WINDOW_HOURS = int(os.getenv("TECH_RADAR_HN_WINDOW_HOURS", "6"))


# ── Semantic Clusters & Term Lists (v3.0) ──────────────────────────────────

# 语义簇：每个簇是独立的领域信号，跨簇命中更重要
CORE_CLUSTERS = {
    "agent": [
        "agent", "agentic", "ai agent", "coding agent", "autonomous agent",
        "multi-agent", "agent framework",
    ],
    "mcp": [
        "mcp", "mcp server", "model context protocol", "agent protocol", "a2a",
    ],
    "devtool": [
        "cli", "terminal", "command line", "devtool", "dev tool",
        "sdk", "plugin", "extension", "cursor", "vscode", "github",
    ],
    "automation": [
        "automation", "workflow", "pipeline", "orchestration",
        "orchestrate", "tool calling", "tool use", "function calling",
    ],
    "ai_eng": [
        "eval", "evaluation", "deployment", "runtime", "sandbox",
        "integration", "local-first", "open-source",
    ],
}

# ── Engineering Value terms ───────────────────────────────────────────────

RUNNABLE_SURFACE = [
    "cli", "terminal", "command line", "sdk", "runtime", "server",
    "desktop", "app", "extension", "plugin", "library", "framework",
]

INTEGRATION_TERMS = [
    "api", "integration", "integrate", "mcp server", "protocol",
    "cursor", "vscode", "github", "browser", "webhook",
]

AUTOMATION_TERMS = [
    "control", "operate", "automate", "execute", "run", "deploy",
    "orchestrate", "workflow", "pipeline", "hands", "computer use",
]

DEV_WORKFLOW_TERMS = [
    "coding agent", "codebase", "repo", "terminal", "command line",
    "local-first", "your code stays", "desktop", "developer",
]

CONCRETE_TARGETS = [
    "ios simulator", "android emulator", "android devices", "desktop",
    "browser", "terminal", "codebase", "files", "github", "cursor",
    "vscode", "computer", "simulator", "emulator",
]

# ── Novelty: AI + concrete target pairs ──────────────────────────────────

NOVEL_PATTERNS = [
    ("agent", "ios simulator"),
    ("agent", "android emulator"),
    ("agent", "desktop"),
    ("agent", "browser"),
    ("agent", "terminal"),
    ("agent", "codebase"),
    ("coding agent", "local-first"),
    ("mcp", "bridge"),
    ("mcp", "server"),
    ("agent", "computer use"),
    ("agent", "hands"),
    ("agent", "eyes"),
]

# ── Penalty & Cap terms ──────────────────────────────────────────────────

NOISE_TERMS = [
    "toy", "demo", "example", "tutorial", "homework", "leetcode",
    "portfolio", "resume", "wallpaper", "boilerplate", "template",
    "clone", "sample", "beginner", "course",
]

COLLECTION_TERMS = [
    "awesome", "curated", "collection", "resources", "guide",
    "catalog", "directory", "awesome-list", "list of", "best of",
]

VAGUE_TERMS = [
    "advisor", "assistant", "orchestrator", "platform", "framework",
]

TYPE_LABELS = {
    "new": "🆕",
    "trend": "📈",
    "collect": "📚",
    "hn": "📰",
}

Item = Dict[str, Any]


# ── Env / IO ────────────────────────────────────────────────────────────────

def load_env() -> None:
    """Load an optional dotenv-format file without touching host-agent secrets."""
    if not ENV_FILE:
        return

    env_file = Path(ENV_FILE).expanduser()
    if not env_file.exists():
        return

    try:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")

            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        return


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_clock() -> str:
    return utc_now().strftime("%H:%M UTC")


def fmt_date(days_ago: int = 0) -> str:
    return (utc_now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def normalize_text(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def load_json_file(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        return data if isinstance(data, dict) else default
    except Exception:
        return default


def load_state() -> Dict[str, Any]:
    state = load_json_file(
        STATE_FILE,
        {
            "seen": [],
            "seen_urls": [],
            "last_checked": None,
        },
    )

    state.setdefault("seen", [])
    state.setdefault("seen_urls", [])
    state.setdefault("last_checked", None)

    return state


def save_state(state: Dict[str, Any]) -> None:
    atomic_write_json(STATE_FILE, state)


def write_pending(items: List[Item], time_utc: str) -> None:
    atomic_write_json(
        PENDING_FILE,
        {
            "generated_at": utc_now().isoformat(),
            "time_utc": time_utc,
            "items": items,
        },
    )


def read_pending() -> Dict[str, Any]:
    return load_json_file(PENDING_FILE, {"items": []})


def clear_pending() -> None:
    try:
        PENDING_FILE.unlink()
    except FileNotFoundError:
        pass


# ── HTTP ────────────────────────────────────────────────────────────────────

def fetch_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 12,
) -> Optional[Dict[str, Any]]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": APP_NAME,
            "Accept": "application/json",
            **(headers or {}),
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
    ):
        return None


def github_cli_path() -> Optional[str]:
    candidates = [
        os.getenv("GH_CLI_PATH"),
        shutil.which("gh"),
        r"C:\Program Files\GitHub CLI\gh.exe",
    ]

    for p in candidates:
        if p and Path(p).exists():
            return p

    return None


def fetch_github_via_gh(
    query: str,
    per_page: int = GITHUB_PER_PAGE,
) -> Optional[Dict[str, Any]]:
    gh = github_cli_path()
    if not gh:
        return None

    try:
        result = subprocess.run(
            [
                gh,
                "api",
                "search/repositories",
                "--method",
                "GET",
                "-f",
                f"q={query}",
                "-f",
                "sort=stars",
                "-f",
                "order=desc",
                "-f",
                f"per_page={per_page}",
            ],
            capture_output=True,
            text=True,
            timeout=18,
        )

        if result.returncode != 0 or not result.stdout.strip():
            return None

        return json.loads(result.stdout)
    except Exception:
        return None


def fetch_github(
    query: str,
    per_page: int = GITHUB_PER_PAGE,
) -> Optional[Dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": per_page,
        }
    )

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = fetch_json(
        f"https://api.github.com/search/repositories?{params}",
        headers=headers,
    )

    if data is not None:
        return data

    return fetch_github_via_gh(query, per_page)


# ── Scoring: 0–100 multi-dimensional engineering value (v3.0) ─────────────

def norm_text(*parts: str) -> str:
    return " ".join(str(p or "").lower() for p in parts)


def contains(text: str, term: str) -> bool:
    term = term.lower()
    if " " in term or "-" in term:
        return term in text
    return re.search(
        rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text
    ) is not None


def count_terms(text: str, terms: list) -> int:
    return sum(1 for t in terms if contains(text, t))


def parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def days_since(value, now: datetime) -> float:
    dt = parse_dt(value)
    if not dt:
        return 365.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400)


def exp_decay(days: float, half_life_like: float) -> float:
    return math.exp(-days / half_life_like)


def log_score(value: float, base: float, scale: float) -> float:
    return scale * math.log1p(max(0, value)) / math.log1p(base)


# ── Dimension A: Domain Relevance (0–100, weight 30%) ────────────────────

def calc_relevance(text: str, topics_text: str = "") -> float:
    cluster_score = 0.0
    weighted_hits = 0.0

    for _, terms in CORE_CLUSTERS.items():
        hits = count_terms(text, terms) + count_terms(topics_text, terms) * 1.5
        if hits:
            cluster_score += 18.0
            weighted_hits += min(8.0, hits * 3.0)

    return min(100.0, cluster_score + weighted_hits)


# ── Dimension B: Engineering Value (0–100, weight 25%) ───────────────────

def calc_engineering(text: str) -> float:
    runnable = min(25.0, count_terms(text, RUNNABLE_SURFACE) * 6.0)
    integration = min(20.0, count_terms(text, INTEGRATION_TERMS) * 5.0)
    automation = min(20.0, count_terms(text, AUTOMATION_TERMS) * 5.0)
    dev_workflow = min(20.0, count_terms(text, DEV_WORKFLOW_TERMS) * 6.0)
    concrete = min(15.0, count_terms(text, CONCRETE_TARGETS) * 5.0)

    return min(100.0, runnable + integration + automation + dev_workflow + concrete)


# ── Dimension C: Maturity & Credibility (0–100, weight 20%) ──────────────

def calc_maturity(item: dict, now: datetime) -> float:
    stars = int(item.get("stargazers_count") or item.get("stars") or 0)
    points = int(item.get("points") or 0)

    if stars > 0:
        age = max(1.0, days_since(item.get("created_at"), now))
        updated = days_since(
            item.get("updated_at") or item.get("created_at"), now
        )

        star_score = min(60.0, log_score(stars, 1000.0, 60.0))
        velocity_score = min(25.0, log_score(stars / age, 20.0, 25.0))
        update_score = 15.0 * exp_decay(updated, 45.0)

        return min(100.0, star_score + velocity_score + update_score)

    if points > 0:
        return min(100.0, log_score(points, 500.0, 70.0))

    return 0.0


# ── Dimension D: Freshness & Momentum (0–100, weight 15%) ────────────────

def calc_freshness(item: dict, source: str, now: datetime) -> float:
    age = days_since(item.get("created_at"), now)
    updated = days_since(
        item.get("updated_at") or item.get("created_at"), now
    )

    created_score = 45.0 * exp_decay(age, 60.0)
    updated_score = 35.0 * exp_decay(updated, 30.0)

    source_score = {
        "trend": 20, "trending": 20,
        "collect": 12, "collection": 12,
        "new": 10,
        "hn": 15, "hackernews": 15,
    }.get(source, 8.0)

    return min(100.0, created_score + updated_score + source_score)


# ── Dimension E: Novelty / Specificity (0–100, weight 10%) ───────────────

def calc_novelty(text: str) -> float:
    pair_hits = sum(
        1 for a, b in NOVEL_PATTERNS
        if contains(text, a) and contains(text, b)
    )

    concrete_hits = count_terms(text, CONCRETE_TARGETS)
    workflow_hits = count_terms(text, DEV_WORKFLOW_TERMS)

    novelty = 0.0
    novelty += min(35.0, pair_hits * 12.0)
    novelty += min(30.0, concrete_hits * 7.0)
    novelty += min(25.0, workflow_hits * 6.0)

    generic_hits = count_terms(text, VAGUE_TERMS)
    if generic_hits >= 2 and concrete_hits == 0 and workflow_hits == 0:
        novelty -= 20.0

    return max(0.0, min(100.0, novelty))


# ── Penalties ────────────────────────────────────────────────────────────

def calc_penalty(text: str, relevance: float, engineering: float, novelty: float) -> float:
    penalty = 0.0

    noise_hits = count_terms(text, NOISE_TERMS)
    penalty += noise_hits * 8.0

    vague_hits = count_terms(text, VAGUE_TERMS)
    concrete_hits = count_terms(text, CONCRETE_TARGETS)
    runnable_hits = count_terms(text, RUNNABLE_SURFACE)

    if vague_hits >= 2 and concrete_hits == 0 and runnable_hits == 0:
        penalty += 10.0

    # Keyword-stacking penalty: high relevance but low eng + low novelty
    if relevance >= 75.0 and engineering < 35.0 and novelty < 30.0:
        penalty += 18.0

    return penalty


def apply_type_caps(score: float, text: str, stars: int) -> float:
    noise_hits = count_terms(text, NOISE_TERMS)
    collection_hits = count_terms(text, COLLECTION_TERMS)

    if noise_hits:
        score = min(score, 50.0)

    if contains(text, "toy") or contains(text, "homework") or contains(text, "leetcode"):
        score = min(score, 42.0)

    if contains(text, "tutorial") or contains(text, "course") or contains(text, "beginner"):
        score = min(score, 52.0)

    if collection_hits:
        score = min(score, 78.0) if stars >= 300 else min(score, 68.0)

    return score


# ── Main scoring function ────────────────────────────────────────────────

def score_item(item: dict, now: Optional[datetime] = None) -> float:
    """Score a single item on 0–100 engineering value scale."""
    now = now or utc_now()

    title = item.get("title") or item.get("full_name") or ""
    desc = item.get("description") or ""
    url = item.get("html_url") or item.get("url") or ""
    source = item.get("source") or item.get("type") or ""
    topics = item.get("topics") or []

    topics_text = " ".join(topics)
    text = norm_text(title, desc, url, topics_text)

    stars = int(item.get("stargazers_count") or item.get("stars") or 0)

    relevance = calc_relevance(text, topics_text)
    engineering = calc_engineering(text)
    maturity = calc_maturity(item, now)
    freshness = calc_freshness(item, source, now)
    novelty = calc_novelty(text)

    score = (
        relevance * 0.30
        + engineering * 0.25
        + maturity * 0.20
        + freshness * 0.15
        + novelty * 0.10
    )

    source_bonus = {
        "trend": 4, "trending": 4,
        "hn": 3, "hackernews": 3,
        "collect": 1, "collection": 1,
        "new": 0,
    }.get(source, 0.0)

    score += source_bonus
    score += -calc_penalty(text, relevance, engineering, novelty)

    # High-value rescue: prevent sim-use / godcoder from being filtered out
    if stars >= 150 and relevance >= 40.0 and engineering >= 20.0 and novelty >= 30.0:
        score = max(score, float(PASS_THRESHOLD))

    score = apply_type_caps(score, text, stars)

    return round(max(0.0, min(100.0, score)), 1)


# ── Select items for delivery ────────────────────────────────────────────

def select_items(items: list, max_items: int = 5, now: Optional[datetime] = None) -> list:
    """Score all items and return the top subset that pass thresholds."""
    now = now or utc_now()

    scored = [{**item, "score": score_item(item, now=now), "engineering": calc_engineering(norm_text(item.get("title") or item.get("full_name") or "", item.get("description") or "", item.get("html_url") or item.get("url") or "", " ".join(item.get("topics") or [])))} for item in items]

    # Dynamic threshold: top 85th percentile, floor at PASS_THRESHOLD
    scores = sorted(x["score"] for x in scored)
    if len(scores) >= 20:
        p85 = scores[int(len(scores) * 0.85)]
        threshold = max(float(PASS_THRESHOLD), p85)
    else:
        threshold = float(PASS_THRESHOLD)

    passed = [x for x in scored if x["score"] >= threshold]

    # Bypass: high-stars + proven engineering items skip dynamic threshold
    bypassed = [
        x for x in scored
        if x["score"] >= float(PASS_THRESHOLD)
        and int(x.get("stargazers_count") or x.get("stars") or 0) >= 200
        and x.get("engineering", 0) >= 25
        and x not in passed
    ]
    passed.extend(bypassed)

    # Fallback: if nothing passes dynamic threshold, try fixed threshold
    if not passed:
        passed = [x for x in scored if x["score"] >= PASS_THRESHOLD]

    # Last resort: pick items ≥ PASS_THRESHOLD with good engineering
    if not passed:
        candidates = sorted(
            [x for x in scored if x["score"] >= float(PASS_THRESHOLD)],
            key=lambda x: x["score"],
            reverse=True,
        )
        if not candidates:
            candidates = sorted(
                [x for x in scored if x["score"] >= CANDIDATE_THRESHOLD],
                key=lambda x: x["score"],
                reverse=True,
            )
        if candidates:
            passed = candidates[:min(3, max_items)]

    result = sorted(passed, key=lambda x: x["score"], reverse=True)[:max_items]

    # Pad to at least 3 items from candidate pool (≥ CANDIDATE_THRESHOLD)
    min_push = min(3, max_items)
    if len(result) < min_push:
        already = {x["id"] for x in result}
        pool = sorted(
            [x for x in scored if x["score"] >= float(CANDIDATE_THRESHOLD) and x["id"] not in already],
            key=lambda x: x["score"],
            reverse=True,
        )
        result.extend(pool[: min_push - len(result)])

    return result[:max_items]


# ── Item builders ───────────────────────────────────────────────────────────

def github_item(repo: Dict[str, Any], source_type: str) -> Optional[Item]:
    try:
        repo_id = repo["id"]
        name = normalize_text(repo["full_name"])
        desc = normalize_text(repo.get("description"))
        url = repo["html_url"]
        stars = int(repo.get("stargazers_count") or 0)

        return {
            "id": f"repo:{repo_id}",
            "type": source_type,
            "emoji": TYPE_LABELS.get(source_type, "?"),
            "score": 0,
            "stars": stars,
            "name": name,
            "title": name,
            "description": desc,
            "original_text": desc or name,
            "url": url,
            "created_at": repo.get("created_at"),
            "updated_at": repo.get("updated_at"),
        }
    except Exception:
        return None


def hn_item(hit: Dict[str, Any]) -> Optional[Item]:
    try:
        oid = str(hit["objectID"])
        title = normalize_text(hit.get("title") or hit.get("story_title"))

        if not title:
            return None

        points = int(hit.get("points") or 0)
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={oid}"

        desc = (
            f"Hacker News discussion with {points} points"
            if points
            else "Hacker News discussion"
        )

        return {
            "id": f"hn:{oid}",
            "type": "hn",
            "emoji": TYPE_LABELS["hn"],
            "score": 0,
            "stars": points,
            "name": title,
            "title": title,
            "description": desc,
            "original_text": f"{title} — {desc}",
            "url": url,
            "created_at": hit.get("created_at"),
            "updated_at": None,
        }
    except Exception:
        return None


# ── Sources ─────────────────────────────────────────────────────────────────

def source_new_repos() -> List[Item]:
    since = fmt_date(2)

    queries = [
        f"topic:ai-agent created:>{since}",
        f"topic:mcp created:>{since}",
        f"topic:agent-framework created:>{since}",
        f"mcp server created:>{since}",
        f"agent framework created:>{since}",
    ]

    out: List[Item] = []

    for q in queries:
        data = fetch_github(q)

        for repo in (data or {}).get("items", []):
            if int(repo.get("stargazers_count") or 0) < NEW_REPO_MIN_STARS:
                continue

            item = github_item(repo, "new")
            if item:
                out.append(item)

    return out


def source_trending() -> List[Item]:
    # GitHub Search cannot return true star velocity without historical snapshots.
    # This source means "fresh repos with high total stars".
    since = fmt_date(TRENDING_SINCE_DAYS)

    queries = [
        f"ai agent created:>{since}",
        f"mcp created:>{since}",
        f"agent framework created:>{since}",
        f"agentic created:>{since}",
    ]

    out: List[Item] = []

    for q in queries:
        data = fetch_github(q)

        for repo in (data or {}).get("items", []):
            if int(repo.get("stargazers_count") or 0) < TRENDING_MIN_STARS:
                continue

            item = github_item(repo, "trend")
            if item:
                out.append(item)

    return out


def source_collections() -> List[Item]:
    since = fmt_date(COLLECTION_SINCE_DAYS)

    queries = [
        f"awesome ai agent created:>{since}",
        f"awesome mcp created:>{since}",
        f"curated ai tools created:>{since}",
        f"agent resources created:>{since}",
    ]

    out: List[Item] = []

    for q in queries:
        data = fetch_github(q)

        for repo in (data or {}).get("items", []):
            if int(repo.get("stargazers_count") or 0) < COLLECTION_MIN_STARS:
                continue

            name = normalize_text(repo.get("full_name"))
            desc = normalize_text(repo.get("description"))

            text = f"{name} {desc}".lower()
            if not count_terms(text, COLLECTION_TERMS):
                continue

            item = github_item(repo, "collect")
            if not item:
                continue

            out.append(item)

    return out


def source_hn() -> List[Item]:
    cutoff = int(time.time()) - HN_WINDOW_HOURS * 3600

    keywords = [
        "AI agent",
        "MCP",
        "agent framework",
        "CLI tool",
        "Show HN",
        "agent",
        "LLM",
        "tool use",
    ]

    out: List[Item] = []

    for kw in keywords:
        params = urllib.parse.urlencode(
            {
                "query": kw,
                "tags": "story",
                "hitsPerPage": HN_PER_PAGE,
                "numericFilters": f"created_at_i>{cutoff}",
            }
        )

        data = fetch_json(
            f"https://hn.algolia.com/api/v1/search_by_date?{params}"
        )

        for hit in (data or {}).get("hits", []):
            if int(hit.get("points") or 0) < HN_MIN_POINTS:
                continue

            item = hn_item(hit)
            if item:
                out.append(item)

    return out


# ── Pipeline ────────────────────────────────────────────────────────────────

def collect_items() -> List[Item]:
    return (
        source_new_repos()
        + source_trending()
        + source_collections()
        + source_hn()
    )


def sort_key(item: Item) -> Any:
    return (
        float(item.get("score") or 0),
        int(item.get("stars") or 0),
        str(item.get("created_at") or ""),
    )


def dedupe_items(items: Iterable[Item]) -> List[Item]:
    seen_urls = set()
    seen_ids = set()
    out: List[Item] = []

    for item in sorted(items, key=sort_key, reverse=True):
        uid = item.get("id")
        url = item.get("url")

        if uid in seen_ids or url in seen_urls:
            continue

        seen_ids.add(uid)
        seen_urls.add(url)
        out.append(item)

    return out


def unseen_items(
    state: Dict[str, Any],
    items: Iterable[Item],
) -> List[Item]:
    seen_ids = set(state.get("seen", []))
    seen_urls = set(state.get("seen_urls", []))

    return [
        i
        for i in dedupe_items(items)
        if i.get("id") not in seen_ids
        and i.get("url") not in seen_urls
    ]


def append_limited(
    existing: List[str],
    additions: Iterable[str],
    limit: int,
) -> List[str]:
    out = list(existing or [])
    known = set(out)

    for x in additions:
        if x and x not in known:
            out.append(x)
            known.add(x)

    return out[-limit:]


def commit_items(items: List[Item]) -> None:
    state = load_state()
    state["last_checked"] = utc_now().isoformat()

    state["seen"] = append_limited(
        state.get("seen", []),
        [i.get("id") for i in items],
        SEEN_RETENTION,
    )

    state["seen_urls"] = append_limited(
        state.get("seen_urls", []),
        [i.get("url") for i in items],
        SEEN_RETENTION,
    )

    save_state(state)


# ── Formatting ──────────────────────────────────────────────────────────────

def meta_text(item: Item) -> str:
    score_val = float(item.get("score") or 0)
    meta = f"[{score_val:.1f}分"

    stars = int(item.get("stars") or 0)
    if stars > 0:
        meta += f" ⭐{stars}"

    return meta + "]"


def display_line(item: Item) -> str:
    return f"{item.get('emoji', '?')} {meta_text(item)} {item.get('name', '').strip()}"


def short_desc(text: str, limit: int = 260) -> str:
    text = normalize_text(text)

    if not text:
        return ""

    first = text.split(". ")[0].strip()
    text = first if first else text

    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"

    return text


def public_item(item: Item) -> Item:
    return {
        "id": item.get("id"),
        "type": item.get("type"),
        "emoji": item.get("emoji"),
        "score": round(float(item.get("score") or 0), 1),
        "stars": int(item.get("stars") or 0),
        "name": item.get("name"),
        "title": item.get("title"),
        "description": item.get("description"),
        "original_text": (
            item.get("original_text")
            or item.get("description")
            or item.get("title")
        ),
        "display_line": display_line(item),
        "url": item.get("url"),
    }


def format_json(items: List[Item], time_utc: str) -> str:
    return json.dumps(
        {
            "time_utc": time_utc,
            "items": [public_item(i) for i in items],
        },
        ensure_ascii=False,
        indent=2,
    )


def format_brief(items: List[Item], time_utc: str) -> str:
    lines = [f"📡 {time_utc}"]

    for item in items:
        lines.append("")
        lines.append(display_line(item))

        desc = short_desc(
            str(
                item.get("original_text")
                or item.get("description")
                or ""
            )
        )

        if desc:
            lines.append(f"   {desc}")

        lines.append(f"   {item.get('url')}")

    return "\n".join(lines).strip()


def print_items(
    items: List[Item],
    as_json: bool,
    time_utc: str,
) -> None:
    if not items:
        return

    print(format_json(items, time_utc) if as_json else format_brief(items, time_utc))


# ── CLI ─────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AI Agent / MCP / CLI / Automation tech radar"
    )

    p.add_argument(
        "--show",
        action="store_true",
        help="Preview unseen items without marking them as seen",
    )

    p.add_argument(
        "--commit",
        action="store_true",
        help="Mark the last pending --show batch as seen",
    )

    p.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON",
    )

    p.add_argument(
        "--max",
        type=int,
        default=MAX_PER_TICK,
        help="Maximum items to output/commit",
    )

    p.add_argument(
        "--no-pending",
        action="store_true",
        help="With --show, do not write pending batch",
    )

    return p.parse_args()


def get_unseen_batch(max_items: int) -> List[Item]:
    state = load_state()
    all_items = collect_items()
    unseen = unseen_items(state, all_items)

    # If unseen pool is insufficient for a meaningful push,
    # supplement with highest-scoring items from all_items
    # so select_items() can apply candidate-padding logic.
    min_push = min(3, max_items)
    if len(unseen) < min_push:
        unseen_ids = {i.get("id") for i in unseen}
        supplement = [
            i for i in all_items
            if i.get("id") not in unseen_ids
        ]
        unseen.extend(supplement[: min_push - len(unseen)])

    return select_items(unseen, max_items)


def main() -> int:
    load_env()
    args = parse_args()
    time_utc = utc_clock()

    if args.commit:
        pending = read_pending()
        items = pending.get("items", [])

        if items:
            commit_items(items)
            clear_pending()

        return 0

    if args.show:
        items = get_unseen_batch(args.max)

        if items and not args.no_pending:
            write_pending([public_item(i) for i in items], time_utc)
        elif not items:
            clear_pending()

        print_items(items, args.json, time_utc)
        return 0

    items = get_unseen_batch(args.max)

    if not items:
        state = load_state()
        state["last_checked"] = utc_now().isoformat()
        save_state(state)
        clear_pending()
        return 0

    commit_items(items)
    clear_pending()
    print_items(items, args.json, time_utc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())