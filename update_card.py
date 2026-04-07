#!/usr/bin/env python3
"""
update_card.py — Fetches live GitHub stats and updates card.svg with real data.
Replaces {{PLACEHOLDER}} tokens in card.svg with fetched values.
"""

import os
import sys
import json
import requests
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

# ── Config ────────────────────────────────────────────────────────────────────
TOKEN    = os.environ["GITHUB_TOKEN"]
USERNAME = os.environ["GITHUB_USERNAME"]
HEADERS  = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
GRAPHQL_URL = "https://api.github.com/graphql"
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
CARD_PATH   = os.path.join(SCRIPT_DIR, "card.svg")

# ── Helpers ───────────────────────────────────────────────────────────────────

def gh_get(path: str) -> dict:
    url = f"https://api.github.com{path}"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def gh_graphql(query: str, variables: dict | None = None) -> dict:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(GRAPHQL_URL, json=payload, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data["data"]


# ── Fetch stats ───────────────────────────────────────────────────────────────

def fetch_user_stats() -> dict:
    user = gh_get(f"/users/{USERNAME}")
    repos_data = gh_get(f"/users/{USERNAME}/repos?per_page=100&type=owner")

    total_stars = sum(r.get("stargazers_count", 0) for r in repos_data)

    return {
        "repos":     user.get("public_repos", 0),
        "followers": user.get("followers", 0),
        "following": user.get("following", 0),
        "stars":     total_stars,
    }


def fetch_commit_count_and_calendar() -> tuple[int, list[list[dict]]]:
    """Returns (total_commits_last_year, last_12_weeks_grid)."""
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          totalCommitContributions
          contributionCalendar {
            weeks {
              contributionDays {
                contributionCount
                date
              }
            }
          }
        }
      }
    }
    """
    data = gh_graphql(query, {"login": USERNAME})
    cc   = data["user"]["contributionsCollection"]
    total_commits = cc["totalCommitContributions"]
    weeks = cc["contributionCalendar"]["weeks"]
    # Take the last 12 weeks
    last_12 = weeks[-12:] if len(weeks) >= 12 else weeks
    return total_commits, last_12


def fetch_loc() -> tuple[int, int]:
    """Returns (added, deleted) lines of code for the profile repo contributor."""
    try:
        stats = gh_get(f"/repos/{USERNAME}/{USERNAME}/stats/contributors")
        if not stats:
            return 0, 0
        for contributor in stats:
            if contributor.get("author", {}).get("login", "").lower() == USERNAME.lower():
                added   = sum(w["a"] for w in contributor.get("weeks", []))
                deleted = sum(w["d"] for w in contributor.get("weeks", []))
                return added, deleted
        return 0, 0
    except Exception:
        return 0, 0


def fetch_top_languages() -> list[tuple[str, float]]:
    """Returns [(lang, percentage), ...] sorted desc, top 5."""
    repos_data = gh_get(f"/users/{USERNAME}/repos?per_page=100&type=owner")
    lang_bytes: dict[str, int] = {}
    for repo in repos_data:
        if repo.get("fork"):
            continue
        lang = repo.get("language")
        if lang:
            lang_bytes[lang] = lang_bytes.get(lang, 0) + (repo.get("size", 1) * 1024)

    total = sum(lang_bytes.values()) or 1
    sorted_langs = sorted(lang_bytes.items(), key=lambda x: x[1], reverse=True)[:5]
    return [(lang, round(bytes_ / total * 100, 1)) for lang, bytes_ in sorted_langs]


# ── SVG builders ──────────────────────────────────────────────────────────────

# Contribution-count → fill color
CONTRIBUTION_COLORS = [
    (0,  "#161b22"),   # none
    (1,  "#0e4429"),   # low
    (4,  "#006d32"),   # medium-low
    (10, "#26a641"),   # medium-high
    (20, "#39d353"),   # high
]

def contribution_color(count: int) -> str:
    color = CONTRIBUTION_COLORS[0][1]
    for threshold, c in CONTRIBUTION_COLORS:
        if count >= threshold:
            color = c
    return color


def build_contribution_grid(weeks: list) -> str:
    """Returns SVG <rect> elements for the 12-week grid."""
    cell = 10   # cell size
    gap  = 2    # gap between cells
    step = cell + gap
    x0   = 408
    y0   = 202

    rects = []
    for wi, week in enumerate(weeks):
        for di, day in enumerate(week.get("contributionDays", [])):
            count = day.get("contributionCount", 0)
            color = contribution_color(count)
            x = x0 + wi * step
            y = y0 + di * step
            title = f"{count} contributions on {day.get('date', '')}"
            rects.append(
                f'  <rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" ry="2" '
                f'fill="{color}"><title>{title}</title></rect>'
            )
    return "\n".join(rects)


# Language colors (a reasonable subset)
LANG_COLORS = {
    "Python":     "#3572A5",
    "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6",
    "HTML":       "#e34c26",
    "CSS":        "#563d7c",
    "Shell":      "#89e051",
    "Go":         "#00ADD8",
    "Rust":       "#dea584",
    "Java":       "#b07219",
    "C#":         "#178600",
    "C++":        "#f34b7d",
    "C":          "#555555",
    "Ruby":       "#701516",
    "Swift":      "#F05138",
    "Kotlin":     "#A97BFF",
    "Dart":       "#00B4AB",
    "PHP":        "#4F5D95",
    "Scala":      "#c22d40",
    "R":          "#198ce7",
    "MATLAB":     "#e16737",
}
DEFAULT_COLOR = "#8b949e"


def build_language_bar(langs: list[tuple[str, float]]) -> tuple[str, str]:
    """Returns (bar_svg, legend_svg) strings."""
    bar_x  = 408
    bar_y  = 322
    bar_w  = 375
    bar_h  = 12

    # Build stacked bar segments
    bar_parts = []
    cursor = bar_x
    for lang, pct in langs:
        seg_w = max(1, round(pct / 100 * bar_w))
        color = LANG_COLORS.get(lang, DEFAULT_COLOR)
        bar_parts.append(
            f'  <rect x="{cursor}" y="{bar_y}" width="{seg_w}" height="{bar_h}" '
            f'rx="0" fill="{color}"/>'
        )
        cursor += seg_w

    # Round corners on first/last
    if bar_parts:
        # Re-do with rounded corners on outer edges only
        bar_parts = []
        cursor = bar_x
        for i, (lang, pct) in enumerate(langs):
            seg_w = max(1, round(pct / 100 * bar_w))
            color = LANG_COLORS.get(lang, DEFAULT_COLOR)
            rx = 0
            if i == 0:
                # left rounded
                bar_parts.append(
                    f'  <rect x="{cursor}" y="{bar_y}" width="{seg_w + 4}" height="{bar_h}" '
                    f'rx="3" fill="{color}"/>'
                )
                bar_parts.append(
                    f'  <rect x="{cursor + 4}" y="{bar_y}" width="{seg_w}" height="{bar_h}" '
                    f'fill="{color}"/>'
                )
            elif i == len(langs) - 1:
                bar_parts.append(
                    f'  <rect x="{cursor}" y="{bar_y}" width="{seg_w}" height="{bar_h}" '
                    f'rx="3" fill="{color}"/>'
                )
            else:
                bar_parts.append(
                    f'  <rect x="{cursor}" y="{bar_y}" width="{seg_w}" height="{bar_h}" '
                    f'fill="{color}"/>'
                )
            cursor += seg_w

    bar_svg = "\n".join(bar_parts)

    # Build legend
    legend_parts = []
    lx, ly = bar_x, bar_y + 22
    for lang, pct in langs:
        color = LANG_COLORS.get(lang, DEFAULT_COLOR)
        legend_parts.append(
            f'  <circle cx="{lx + 5}" cy="{ly + 5}" r="4" fill="{color}"/>'
            f'  <text x="{lx + 13}" y="{ly + 10}" fill="#8b949e" '
            f'font-size="10" font-family="\'JetBrains Mono\', monospace">'
            f'{lang} {pct}%</text>'
        )
        lx += len(lang) * 7 + 50
        if lx > 740:
            lx = bar_x
            ly += 16

    legend_svg = "\n".join(legend_parts)
    return bar_svg, legend_svg


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Fetching stats for: {USERNAME}")

    stats        = fetch_user_stats()
    commits, weeks = fetch_commit_count_and_calendar()
    loc_add, loc_del = fetch_loc()
    langs        = fetch_top_languages()

    print(f"  Repos: {stats['repos']}, Stars: {stats['stars']}, Commits: {commits}")
    print(f"  LOC +{loc_add} / -{loc_del}")
    print(f"  Languages: {langs}")

    grid_svg             = build_contribution_grid(weeks)
    lang_bar_svg, legend = build_language_bar(langs)

    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    replacements = {
        "{{REPOS}}":             str(stats["repos"]),
        "{{COMMITS}}":           str(commits),
        "{{STARS}}":             str(stats["stars"]),
        "{{FOLLOWERS}}":         str(stats["followers"]),
        "{{FOLLOWING}}":         str(stats["following"]),
        "{{LOC_ADDED}}":         f"+{loc_add:,}",
        "{{LOC_DELETED}}":       f"-{loc_del:,}",
        "{{CONTRIBUTION_GRID}}": grid_svg,
        "{{LANGUAGE_BAR}}":      lang_bar_svg,
        "{{LANGUAGE_LEGEND}}":   legend,
        "{{UPDATED_AT}}":        updated_at,
    }

    with open(CARD_PATH, "r", encoding="utf-8") as f:
        svg = f.read()

    for token, value in replacements.items():
        svg = svg.replace(token, value)

    with open(CARD_PATH, "w", encoding="utf-8") as f:
        f.write(svg)

    print(f"card.svg updated successfully at {updated_at}")


if __name__ == "__main__":
    main()
