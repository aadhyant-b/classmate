"""Extracts professor names from Reddit posts and matches them against RateMyProfessor."""
import collections
import json
import logging
import pathlib
import re

from backend import rmp_client

logger = logging.getLogger(__name__)

_BASE = pathlib.Path(__file__).parent

try:
    with open(_BASE / "schools.json") as f:
        _SLUG_TO_RMP_NAME: dict[str, str] = {
            s["slug"]: s["rmp_school_name"]
            for s in json.load(f)
        }
except (FileNotFoundError, json.JSONDecodeError) as e:
    raise RuntimeError(f"Failed to load schools.json: {e}")

# Words that look like proper nouns but are not professor names
_STOP_WORDS: set[str] = {
    "Final", "Exam", "Homework", "Class", "Course", "Professor", "Instructor",
    "Lecture", "Syllabus", "Project", "Midterm", "Quiz", "Test", "Labs", "Lab",
    "ITCS", "MATH", "STAT", "CHEM", "PSYC", "UWRT", "COMP", "CSCI", "CSC",
    "CS", "IT", "AI", "ML", "DB",
    "Spring", "Fall", "Summer", "Winter", "Semester", "Canvas", "Blackboard",
    "Section", "Credit", "Hours", "School", "College", "University", "Department",
    "Office", "Next", "Last", "This", "First", "Second", "Third",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "June", "July", "August",
    "September", "October", "November", "December",
    "Charlotte", "Chapel", "Hill", "Raleigh", "Carolina",
    "Good", "Great", "Easy", "Hard", "Intro", "Advanced", "General",
}

# Explicit-title patterns — weighted higher in counting
_TITLED = re.compile(
    r'(?:Prof\.?|Professor|Dr\.?)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)',
    re.IGNORECASE,
)
# "with/took/had/take/taking CapName"
_CONTEXTUAL = re.compile(
    r'\b(?:with|took|had|taking|take)\s+([A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})?)\b',
)
# "Last, First" format → normalised to "First Last"
_LAST_FIRST = re.compile(r'\b([A-Z][a-zA-Z]+),\s+([A-Z][a-zA-Z]+)\b')
# Two consecutive title-case words (broad heuristic, lowest weight)
_TWO_CAP = re.compile(r'\b([A-Z][a-z]{3,15})\s+([A-Z][a-z]{3,15})\b')


def _is_stop(name: str) -> bool:
    return any(word in _STOP_WORDS for word in name.split())


def extract_professor_names(posts: list[dict]) -> list[str]:
    """Scan post titles and bodies for professor name candidates. Returns top 5 by mentions."""
    counts: collections.Counter = collections.Counter()

    for post in posts:
        text = f"{post.get('title', '')} {post.get('body', '')}"

        for m in _TITLED.finditer(text):
            name = m.group(1).strip()
            if name[0].isupper() and not _is_stop(name):
                counts[name] += 2

        for m in _CONTEXTUAL.finditer(text):
            name = m.group(1).strip()
            if not _is_stop(name):
                counts[name] += 1

        for m in _LAST_FIRST.finditer(text):
            name = f"{m.group(2)} {m.group(1)}"
            if not _is_stop(name):
                counts[name] += 2

        for m in _TWO_CAP.finditer(text):
            first, last = m.group(1), m.group(2)
            if first not in _STOP_WORDS and last not in _STOP_WORDS:
                counts[f"{first} {last}"] += 1

    return [name for name, _ in counts.most_common(5)]


def get_professor_data(school_slug: str, professor_name: str) -> dict | None:
    """Look up a professor on RMP and return their data + reviews, or None if not found."""
    rmp_school_name = _SLUG_TO_RMP_NAME.get(school_slug)
    if not rmp_school_name:
        logger.warning("Unknown school slug: %r", school_slug)
        return None

    try:
        school_id = rmp_client.get_rmp_school_id(rmp_school_name)
    except ValueError as e:
        logger.warning("Could not get RMP school ID for %r: %s", rmp_school_name, e)
        return None

    professor = rmp_client.search_professor(school_id, professor_name)
    if not professor:
        return None

    reviews = rmp_client.get_professor_reviews(professor["id"])
    return {**professor, "reviews": reviews}


def match_professors(school_slug: str, posts: list[dict], course_code: str) -> list[dict]:
    """Extract professor names from posts, match on RMP, return sorted by num_ratings."""
    candidates = extract_professor_names(posts)
    logger.info(
        "Extracted %d candidate names from %d posts: %s",
        len(candidates), len(posts), candidates,
    )

    results: list[dict] = []
    seen_ids: set[str] = set()

    for name in candidates:
        prof = get_professor_data(school_slug, name)
        if prof and prof["id"] not in seen_ids:
            seen_ids.add(prof["id"])
            results.append(prof)

    results.sort(key=lambda p: p.get("num_ratings") or 0, reverse=True)
    return results


if __name__ == "__main__":
    from backend import reddit_client
    posts = reddit_client.get_professor_posts("UNCCharlotte", "ITCS 1213", "ITCS 1213", limit=10)
    print(f"Got {len(posts)} posts from Reddit")
    professors = match_professors("uncc", posts, "ITCS 1213")
    print(f"Matched {len(professors)} professors on RMP:")
    for p in professors:
        print(f"  {p['name']} — {p['num_ratings']} ratings, {p['rating']}/5")
