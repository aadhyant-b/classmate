"""
Full RMP scrape: fetch all professors with >=3 ratings for UNCC, UNC, NCSU
and add missing ones to faculty_cache.json.

Uses the inline GQL format that the RMP endpoint actually accepts.
Checkpoints every 25 professors per school.
"""
import json
import re
import sys
import time

import requests

from backend.rmp_client import (
    _GRAPHQL_URL,
    _HEADERS,
    get_professor_reviews,
    get_rmp_school_id,
)


def _log(msg):
    print(msg, flush=True)


def get_all_rmp_professors(school_id: str) -> list[dict]:
    """Paginate through ALL professors at a school using the inline query format."""
    all_profs = []
    cursor = None
    page = 0
    consecutive_errors = 0

    while True:
        after = f' after: "{cursor}",' if cursor else ""
        gql = (
            f"{{ newSearch {{ teachers("
            f'query: {{text: "" schoolID: "{school_id}"}} first: 20{after}'
            f") {{ edges {{ node {{ id firstName lastName department "
            f"numRatings avgRating avgDifficulty wouldTakeAgainPercent }} }} "
            f"pageInfo {{ hasNextPage endCursor }} }} }} }}"
        )
        try:
            resp = requests.post(
                _GRAPHQL_URL,
                json={"query": gql},
                headers=_HEADERS,
                timeout=12,
            )
            if not resp.ok:
                _log(f"  HTTP {resp.status_code} on page {page}, retrying in 10s")
                time.sleep(10)
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    _log("  5 consecutive errors — aborting pagination")
                    break
                continue

            data = resp.json()
            teachers = (
                data.get("data", {}).get("newSearch", {}).get("teachers", {})
            )
            edges = teachers.get("edges", [])
            for edge in edges:
                node = edge["node"]
                if node.get("numRatings", 0) >= 3:
                    all_profs.append(node)

            page_info = teachers.get("pageInfo", {})
            consecutive_errors = 0
            page += 1
            if page % 20 == 0:
                _log(f"  Page {page}: {len(all_profs)} profs so far…")

            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            time.sleep(0.15)

        except Exception as exc:
            _log(f"  Exception on page {page}: {exc} — retrying in 8s")
            time.sleep(8)
            consecutive_errors += 1
            if consecutive_errors >= 5:
                _log("  5 consecutive errors — aborting pagination")
                break

    return all_profs


def normalize_course(raw: str) -> str | None:
    """'CSC116', 'CSC-116', 'MA 241' → 'CSC 116' etc. Returns None if unrecognisable."""
    s = re.sub(r"[\s\-_]", "", (raw or "").upper())
    m = re.match(r"^([A-Z]{2,6})(\d{3,4}[A-Z]?)$", s)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return None


def main():
    cache_path = "backend/faculty_cache.json"
    cache = json.load(open(cache_path))

    schools = [
        ("uncc", "University of North Carolina at Charlotte"),
        ("unc",  "University of North Carolina at Chapel Hill"),
        ("ncsu", "North Carolina State University"),
    ]

    grand_total = 0

    for cache_key, school_name in schools:
        _log(f"\n{'='*60}")
        _log(f"  {school_name}  ({cache_key})")
        _log(f"{'='*60}")

        sid = get_rmp_school_id(school_name)
        _log(f"  School ID: {sid}")

        # Build set of already-cached RMP ids for this school
        cached_ids = {
            p["rmp_id"]
            for profs in cache.get(cache_key, {}).values()
            for p in profs
            if "rmp_id" in p
        }
        _log(f"  Already in cache: {len(cached_ids)} professors")

        _log("  Fetching full professor list from RMP…")
        all_rmp = get_all_rmp_professors(sid)
        missing = [p for p in all_rmp if p["id"] not in cached_ids]
        _log(
            f"  RMP total (>=3 ratings): {len(all_rmp)} | "
            f"Missing from cache: {len(missing)}"
        )

        if not missing:
            _log("  Nothing to add.")
            continue

        added = 0
        skipped_no_recent = 0

        for i, prof in enumerate(missing):
            try:
                rmp_id = prof["id"]
                name = " ".join(
                    f"{prof.get('firstName', '')} {prof.get('lastName', '')}".split()
                )
                dept = (prof.get("department") or "Unknown").strip()

                reviews_raw = get_professor_reviews(rmp_id, limit=100)
                recent = [
                    r for r in reviews_raw
                    if (r.get("date") or "")[:4] >= "2022"
                ]

                if not recent:
                    skipped_no_recent += 1
                    time.sleep(0.1)
                    continue

                courses = sorted(set(
                    c
                    for r in recent
                    for c in [normalize_course(r.get("class_name", ""))]
                    if c
                ))

                last_reviewed = max(
                    (r.get("date", "") for r in recent), default=""
                )

                entry = {
                    "name":            name,
                    "rmp_id":          rmp_id,
                    "rating":          prof.get("avgRating"),
                    "difficulty":      prof.get("avgDifficulty"),
                    "num_ratings":     prof.get("numRatings"),
                    "total_ratings":   prof.get("numRatings"),
                    "would_take_again": prof.get("wouldTakeAgainPercent"),
                    "department":      dept,
                    "courses_taught":  courses,
                    "last_reviewed":   last_reviewed,
                    "reviews":         recent[:20],
                }

                cache.setdefault(cache_key, {}).setdefault(dept, []).append(entry)
                added += 1
                grand_total += 1

                if added % 25 == 0:
                    json.dump(cache, open(cache_path, "w"), indent=2)
                    _log(
                        f"  [{cache_key}] checkpoint: {added}/{len(missing)} added "
                        f"({skipped_no_recent} skipped, no recent reviews)"
                    )

                time.sleep(0.3)

            except KeyboardInterrupt:
                _log("\nInterrupted — saving checkpoint…")
                json.dump(cache, open(cache_path, "w"), indent=2)
                sys.exit(0)
            except Exception as exc:
                _log(f"  Error on {prof.get('firstName')} {prof.get('lastName')}: {exc}")
                time.sleep(1)
                continue

        # Final save for this school
        json.dump(cache, open(cache_path, "w"), indent=2)
        _log(
            f"  Done: {added} added, {skipped_no_recent} skipped "
            f"(no reviews since 2022)"
        )

    _log(f"\n{'='*60}")
    _log(f"  Grand total added: {grand_total}")
    _log(f"{'='*60}")
    json.dump(cache, open(cache_path, "w"), indent=2)
    _log("Cache saved.")


if __name__ == "__main__":
    main()
