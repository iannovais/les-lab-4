from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
OUT_PATH = ROOT / "data" / "processed" / "dashboard_data.json"

COMMIT_FILES = [
    RAW_DIR / "0-250-commits_metodologia.csv",
    RAW_DIR / "251-500-commits_metodologia.csv",
]
PR_FILE = RAW_DIR / "prs_metodologia.csv"
REPO_FILE = RAW_DIR / "repos_python_populares.csv"
COMPLEXITY_FILE = RAW_DIR / "commits_complexidade.csv"

SIZE_ORDER = ["small", "medium", "large"]


def parse_bool(value: str) -> bool:
    return value.strip().lower() == "true"


def parse_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def commit_size_class(files_changed: int, loc_modified: int) -> str:
    if files_changed <= 5 and loc_modified <= 25:
        return "small"
    if files_changed > 5 and loc_modified > 125:
        return "large"
    return "medium"


def pr_size_class(files_changed: int, loc_modified: int) -> str:
    if files_changed <= 5 and loc_modified <= 25:
        return "small"
    if files_changed > 5 or loc_modified > 125:
        return "large"
    return "medium"


def safe_rate(numer: int, denom: int) -> float | None:
    if denom == 0:
        return None
    return round(numer / denom, 4)


def safe_median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.median(values), 4)


def update_range(current_min: datetime | None, current_max: datetime | None, value: datetime | None) -> tuple[datetime | None, datetime | None]:
    if value is None:
        return current_min, current_max
    if current_min is None or value < current_min:
        current_min = value
    if current_max is None or value > current_max:
        current_max = value
    return current_min, current_max


def read_complexity_keys() -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    with COMPLEXITY_FILE.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            keys.add((row.get("repo_name", "").strip(), row.get("commit_sha", "").strip()))
    return keys


def process_commits(complexity_keys: set[tuple[str, str]]):
    q1_counts = {size: {"total": 0, "bug_fix": 0} for size in SIZE_ORDER}
    q3_counts = {size: {"total": 0, "revert": 0} for size in SIZE_ORDER}
    commit_counts: Counter[str] = Counter()
    total_commits = 0
    commit_date_min = None
    commit_date_max = None
    complexity_lookup: dict[tuple[str, str], dict[str, int | str]] = {}

    for path in COMMIT_FILES:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                repo_name = row.get("repo_name", "").strip()
                commit_sha = row.get("commit_sha", "").strip()
                files_changed = parse_int(row.get("files_changed", "0"))
                loc_modified = parse_int(row.get("total_loc_modified", "0"))
                is_bug_fix = parse_bool(row.get("is_bug_fix", "False"))
                is_revert = parse_bool(row.get("is_revert", "False"))
                commit_date = parse_iso(row.get("date", ""))

                size_class = commit_size_class(files_changed, loc_modified)
                q1_counts[size_class]["total"] += 1
                if is_bug_fix:
                    q1_counts[size_class]["bug_fix"] += 1

                q3_counts[size_class]["total"] += 1
                if is_revert:
                    q3_counts[size_class]["revert"] += 1

                commit_counts[repo_name] += 1
                total_commits += 1
                commit_date_min, commit_date_max = update_range(commit_date_min, commit_date_max, commit_date)

                key = (repo_name, commit_sha)
                if key in complexity_keys:
                    complexity_lookup[key] = {
                        "size": size_class,
                        "loc": loc_modified,
                    }

    q1_items = []
    for size in SIZE_ORDER:
        total = q1_counts[size]["total"]
        bug_fix = q1_counts[size]["bug_fix"]
        q1_items.append(
            {
                "size": size,
                "total": total,
                "bug_fix": bug_fix,
                "non_bug_fix": total - bug_fix,
                "bug_fix_rate": safe_rate(bug_fix, total),
            }
        )

    q3_revert_items = []
    for size in SIZE_ORDER:
        total = q3_counts[size]["total"]
        reverts = q3_counts[size]["revert"]
        q3_revert_items.append(
            {
                "size": size,
                "total": total,
                "revert": reverts,
                "revert_rate": safe_rate(reverts, total),
            }
        )

    return {
        "q1": q1_items,
        "q3_reverts": q3_revert_items,
        "commit_counts": commit_counts,
        "total_commits": total_commits,
        "commit_date_min": commit_date_min,
        "commit_date_max": commit_date_max,
        "complexity_lookup": complexity_lookup,
    }


def process_prs():
    metrics = {
        size: {
            "comments": [],
            "comment_density": [],
            "first_review_min": [],
            "close_hours": [],
            "count": 0,
        }
        for size in SIZE_ORDER
    }
    pr_count = 0
    pr_date_min = None
    pr_date_max = None

    with PR_FILE.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            additions = parse_int(row.get("additions", "0"))
            deletions = parse_int(row.get("deletions", "0"))
            changed_files = parse_int(row.get("changed_files", "0"))
            loc_modified = additions + deletions

            created_at = parse_iso(row.get("created_at", ""))
            first_review_at = parse_iso(row.get("first_review_at", ""))
            closed_at = parse_iso(row.get("closed_at", ""))

            size_class = pr_size_class(changed_files, loc_modified)
            comments = parse_int(row.get("comments", "0"))
            review_comments = parse_int(row.get("review_comments", "0"))
            comments_total = comments + review_comments

            metrics[size_class]["count"] += 1
            metrics[size_class]["comments"].append(float(comments_total))
            if loc_modified > 0:
                metrics[size_class]["comment_density"].append(comments_total / loc_modified)

            if created_at and first_review_at:
                delta_min = (first_review_at - created_at).total_seconds() / 60
                if delta_min >= 0:
                    metrics[size_class]["first_review_min"].append(delta_min)

            if created_at and closed_at:
                delta_hours = (closed_at - created_at).total_seconds() / 3600
                if delta_hours >= 0:
                    metrics[size_class]["close_hours"].append(delta_hours)

            pr_count += 1
            pr_date_min, pr_date_max = update_range(pr_date_min, pr_date_max, created_at)

    pr_items = []
    for size in SIZE_ORDER:
        pr_items.append(
            {
                "size": size,
                "pr_count": metrics[size]["count"],
                "comments_median": safe_median(metrics[size]["comments"]),
                "comments_density_median": safe_median(metrics[size]["comment_density"]),
                "first_review_median_min": safe_median(metrics[size]["first_review_min"]),
                "close_median_hours": safe_median(metrics[size]["close_hours"]),
            }
        )

    return {
        "pr_items": pr_items,
        "pr_count": pr_count,
        "pr_date_min": pr_date_min,
        "pr_date_max": pr_date_max,
    }


def process_complexity(complexity_lookup: dict[tuple[str, str], dict[str, int | str]]):
    metrics = {
        size: {
            "cc_mean": [],
            "cc_max": [],
            "cc_density": [],
            "commit_count": 0,
        }
        for size in SIZE_ORDER
    }

    with COMPLEXITY_FILE.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = (row.get("repo_name", "").strip(), row.get("commit_sha", "").strip())
            if key not in complexity_lookup:
                continue

            files_analyzed = parse_int(row.get("files_analyzed", "0"))
            if files_analyzed == 0:
                continue

            size_class = str(complexity_lookup[key]["size"])
            loc_modified = int(complexity_lookup[key]["loc"])
            cc_mean = parse_float(row.get("cc_mean", "0"))
            cc_max = parse_float(row.get("cc_max", "0"))

            metrics[size_class]["cc_mean"].append(cc_mean)
            metrics[size_class]["cc_max"].append(cc_max)
            if loc_modified > 0:
                metrics[size_class]["cc_density"].append(cc_mean / loc_modified)
            metrics[size_class]["commit_count"] += 1

    items = []
    for size in SIZE_ORDER:
        items.append(
            {
                "size": size,
                "commit_count": metrics[size]["commit_count"],
                "cc_mean_median": safe_median(metrics[size]["cc_mean"]),
                "cc_max_median": safe_median(metrics[size]["cc_max"]),
                "cc_density_median": safe_median(metrics[size]["cc_density"]),
            }
        )

    return items


def process_repos(commit_counts: Counter[str]):
    repos = []
    with REPO_FILE.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            repo_name = f"{row.get('owner', '').strip()}/{row.get('name', '').strip()}"
            repos.append(
                {
                    "repo": repo_name,
                    "stars": parse_int(row.get("stars", "0")),
                    "contributors": parse_int(row.get("contributors", "0")),
                    "commits_5_years": parse_int(row.get("commits_5_anos", "0")),
                }
            )

    repo_count = len(repos)
    top_by_stars = sorted(repos, key=lambda item: item["stars"], reverse=True)[:10]
    top_by_commits = [
        {"repo": name, "commits": count}
        for name, count in commit_counts.most_common(10)
    ]

    bins = [
        ("<1k", 0, 1000),
        ("1k-10k", 1000, 10000),
        ("10k-50k", 10000, 50000),
        ("50k-100k", 50000, 100000),
        ("100k+", 100000, None),
    ]
    hist_counts = []
    for label, start, end in bins:
        count = 0
        for repo in repos:
            stars = repo["stars"]
            if end is None:
                if stars >= start:
                    count += 1
            else:
                if start <= stars < end:
                    count += 1
        hist_counts.append({"bin": label, "count": count})

    return {
        "repo_count": repo_count,
        "top_by_stars": top_by_stars,
        "top_by_commits": top_by_commits,
        "stars_hist": hist_counts,
    }


def main() -> None:
    complexity_keys = read_complexity_keys()
    commit_data = process_commits(complexity_keys)
    pr_data = process_prs()
    complexity_items = process_complexity(commit_data["complexity_lookup"])
    repo_data = process_repos(commit_data["commit_counts"])

    dashboard_data = {
        "dataset": {
            "repo_count": repo_data["repo_count"],
            "commit_count": commit_data["total_commits"],
            "pr_count": pr_data["pr_count"],
            "commit_date_range": {
                "min": commit_data["commit_date_min"].isoformat() if commit_data["commit_date_min"] else None,
                "max": commit_data["commit_date_max"].isoformat() if commit_data["commit_date_max"] else None,
            },
            "pr_date_range": {
                "min": pr_data["pr_date_min"].isoformat() if pr_data["pr_date_min"] else None,
                "max": pr_data["pr_date_max"].isoformat() if pr_data["pr_date_max"] else None,
            },
            "top_repos_by_stars": repo_data["top_by_stars"],
            "top_repos_by_commits": repo_data["top_by_commits"],
            "stars_hist": repo_data["stars_hist"],
        },
        "q1": {
            "size_class_counts": commit_data["q1"],
        },
        "q2": {
            "size_class_metrics": pr_data["pr_items"],
        },
        "q3": {
            "size_class_reverts": commit_data["q3_reverts"],
            "size_class_complexity": complexity_items,
        },
        "notes": {
            "commit_size_rule": "small: files<=5 and loc<=25; large: files>5 and loc>125; medium: otherwise",
            "pr_size_rule": "small: files<=5 and loc<=25; large: files>5 or loc>125; medium: otherwise",
        },
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as handle:
        json.dump(dashboard_data, handle, indent=2)

    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
