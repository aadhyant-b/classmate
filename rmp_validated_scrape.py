"""
Validated RMP expansion: only add professors whose RMP course codes exist
in this school's courses.json catalog entry.

Pagination uses the inline GQL format confirmed to work against RMP's endpoint.
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


def _log(msg: str) -> None:
    print(msg, flush=True)


# ── catalog + cache ────────────────────────────────────────────────────────

courses_catalog: dict = json.load(open("backend/courses.json"))
cache: dict = json.load(open("backend/faculty_cache.json"))

# Build set of normalised codes (no spaces, uppercase) per school
valid_codes: dict[str, set[str]] = {}
for school, courses in courses_catalog.items():
    valid_codes[school] = {c["code"].replace(" ", "").upper() for c in courses}
    _log(f"{school}: {len(valid_codes[school])} valid course codes in catalog")


# ── helpers ────────────────────────────────────────────────────────────────

def normalize_and_validate(raw: str, school: str) -> str | None:
    """Normalise a raw RMP class string and return 'PREFIX NNN' if it exists
    in that school's catalog, else None."""
    s = re.sub(r"[\s\-_]", "", (raw or "").upper())
    m = re.match(r"^([A-Z]{2,6})(\d{3,4}[A-Z]?)$", s)
    if not m:
        return None
    code_nospace = m.group(1) + m.group(2)
    if code_nospace in valid_codes.get(school, set()):
        return f"{m.group(1)} {m.group(2)}"
    return None


def get_all_rmp_professors(school_id: str) -> list[dict]:
    """Paginate through all rated (>=5 ratings) professors at a school."""
    all_profs: list[dict] = []
    cursor: str | None = None
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
                    _log("  5 consecutive errors — stopping pagination")
                    break
                continue

            data = resp.json()
            teachers = (
                data.get("data", {})
                    .get("newSearch", {})
                    .get("teachers", {})
            )
            edges = teachers.get("edges", [])
            for edge in edges:
                node = edge["node"]
                if node.get("numRatings", 0) >= 5:
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

        except KeyboardInterrupt:
            _log("\nInterrupted during pagination — returning collected profs")
            break
        except Exception as exc:
            _log(f"  Exception on page {page}: {exc} — retrying in 8s")
            time.sleep(8)
            consecutive_errors += 1
            if consecutive_errors >= 5:
                _log("  5 consecutive errors — stopping pagination")
                break

    return all_profs


# ── main ───────────────────────────────────────────────────────────────────

SCHOOLS = [
    ("uncc", "University of North Carolina at Charlotte"),
    ("unc",  "University of North Carolina at Chapel Hill"),
    ("ncsu", "North Carolina State University"),
]

CACHE_PATH = "backend/faculty_cache.json"
grand_added = 0

for cache_key, school_name in SCHOOLS:
    _log(f"\n{'='*60}")
    _log(f"  {school_name}  ({cache_key})")
    _log(f"{'='*60}")

    sid = get_rmp_school_id(school_name)
    _log(f"  School ID: {sid}")

    cached_ids: set[str] = {
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
        f"  RMP total (>=5 ratings): {len(all_rmp)} | "
        f"Missing from cache: {len(missing)}"
    )

    if not missing:
        _log("  Nothing to add.")
        continue

    added = skipped_stale = skipped_no_courses = 0

    for prof in missing:
        try:
            rmp_id  = prof["id"]
            name    = " ".join(
                f"{prof.get('firstName','')} {prof.get('lastName','')}".split()
            )
            dept    = (prof.get("department") or "Unknown").strip()

            reviews_raw = get_professor_reviews(rmp_id, limit=100)
            recent = [r for r in reviews_raw if (r.get("date") or "")[:4] >= "2022"]

            if not recent:
                skipped_stale += 1
                time.sleep(0.1)
                continue

            # Validate every course code against this school's catalog
            valid_courses = sorted(set(
                c
                for r in recent
                for c in [normalize_and_validate(r.get("class_name", ""), cache_key)]
                if c
            ))

            if not valid_courses:
                skipped_no_courses += 1
                time.sleep(0.1)
                continue

            entry = {
                "name":            name,
                "rmp_id":          rmp_id,
                "rating":          prof.get("avgRating"),
                "difficulty":      prof.get("avgDifficulty"),
                "num_ratings":     prof.get("numRatings"),
                "total_ratings":   prof.get("numRatings"),
                "would_take_again": prof.get("wouldTakeAgainPercent"),
                "department":      dept,
                "courses_taught":  valid_courses,
                "last_reviewed":   max(
                    (r.get("date", "") for r in recent), default=""
                ),
                "reviews":         recent[:20],
            }

            cache.setdefault(cache_key, {}).setdefault(dept, []).append(entry)
            added    += 1
            grand_added += 1

            if added % 25 == 0:
                json.dump(cache, open(CACHE_PATH, "w"), indent=2)
                _log(
                    f"  [{cache_key}] checkpoint {added}/{len(missing)} — "
                    f"stale={skipped_stale} no-catalog={skipped_no_courses}"
                )

            time.sleep(0.3)

        except KeyboardInterrupt:
            _log("\nInterrupted — saving checkpoint…")
            json.dump(cache, open(CACHE_PATH, "w"), indent=2)
            sys.exit(0)
        except Exception as exc:
            _log(f"  Error on {prof.get('firstName')} {prof.get('lastName')}: {exc}")
            time.sleep(1)
            continue

    json.dump(cache, open(CACHE_PATH, "w"), indent=2)
    _log(
        f"  Done: {added} added | "
        f"{skipped_stale} stale (no 2022+ reviews) | "
        f"{skipped_no_courses} no catalog match"
    )

_log(f"\n{'='*60}")
_log(f"  Grand total added: {grand_added}")
_log(f"{'='*60}")
json.dump(cache, open(CACHE_PATH, "w"), indent=2)
_log("Cache saved.")
