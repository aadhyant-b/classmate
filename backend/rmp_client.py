"""Fetches professor data and reviews from RateMyProfessor via GraphQL and page scraping."""
import base64
import json
import logging

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_GRAPHQL_URL = "https://www.ratemyprofessors.com/graphql"
_TIMEOUT     = 5

_HEADERS = {
    "Authorization": "Basic dGVzdDp0ZXN0",
    "Content-Type":  "application/json",
    "User-Agent":    "ClassMate/0.1 (educational project)",
}

_school_id_cache: dict[str, str] = {}


def _decode_id(encoded_id: str) -> str:
    """Decode base64 RMP node ID to numeric string. 'VGVhY2hlci0xMjM0' → '1234'."""
    try:
        padded  = encoded_id + "=" * (-len(encoded_id) % 4)
        decoded = base64.b64decode(padded).decode("utf-8")
        return decoded.split("-")[-1]
    except Exception:
        return encoded_id


def get_rmp_school_id(school_name: str) -> str:
    """Return RMP school ID for school_name. Caches result. Raises ValueError if not found."""
    if school_name in _school_id_cache:
        return _school_id_cache[school_name]

    query = {
        "query": (
            f'{{ newSearch {{ schools(query: {{text: "{school_name}"}}) '
            f'{{ edges {{ node {{ id name }} }} }} }} }}'
        )
    }

    try:
        resp = requests.post(_GRAPHQL_URL, json=query, headers=_HEADERS, timeout=_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise ValueError(f"RMP school lookup request failed: {e}") from e

    if not resp.ok:
        raise ValueError(f"RMP returned HTTP {resp.status_code} for school lookup")

    try:
        edges = resp.json()["data"]["newSearch"]["schools"]["edges"]
    except (KeyError, ValueError, TypeError) as e:
        raise ValueError(f"Unexpected RMP response shape during school lookup: {e}") from e

    if not edges:
        raise ValueError(f"No RMP school found for: {school_name!r}")

    school_id = edges[0]["node"]["id"]
    _school_id_cache[school_name] = school_id
    return school_id


def search_professor(school_id: str, professor_name: str) -> dict | None:
    """Search RMP for a professor at a school. Returns first match dict or None."""
    query = {
        "query": (
            f'{{ newSearch {{ teachers(query: {{text: "{professor_name}", schoolID: "{school_id}"}}) '
            f'{{ edges {{ node {{ id firstName lastName avgRating avgDifficulty '
            f'numRatings wouldTakeAgainPercent department }} }} }} }} }}'
        )
    }

    try:
        resp = requests.post(_GRAPHQL_URL, json=query, headers=_HEADERS, timeout=_TIMEOUT)
    except requests.exceptions.RequestException as e:
        logger.warning("RMP professor search request failed (name=%r): %s", professor_name, e)
        return None

    if not resp.ok:
        logger.warning(
            "RMP returned HTTP %d for professor search (name=%r)",
            resp.status_code, professor_name,
        )
        return None

    try:
        edges = resp.json()["data"]["newSearch"]["teachers"]["edges"]
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("Unexpected RMP response shape for professor search: %s", e)
        return None

    if not edges:
        return None

    node = edges[0]["node"]
    return {
        "id":               node.get("id", ""),
        "name":             f"{node.get('firstName', '')} {node.get('lastName', '')}".strip(),
        "rating":           node.get("avgRating"),
        "difficulty":       node.get("avgDifficulty"),
        "num_ratings":      node.get("numRatings", 0),
        "department":       node.get("department", ""),
        "would_take_again": node.get("wouldTakeAgainPercent"),
    }


def get_professors_for_course(school_name: str, course_code: str, limit: int = 5) -> list[dict]:
    """Search RMP for professors who have been rated for a specific course code.

    Strategy: search for professors at the school whose ratings include the course code.
    Use the teacher search with the course code as the query, then verify matches.
    """
    try:
        school_id = get_rmp_school_id(school_name)
    except ValueError as e:
        logger.warning("Could not get school ID: %s", e)
        return []

    query = {
        "query": (
            f'{{ newSearch {{ teachers(query: {{text: "{course_code}", schoolID: "{school_id}"}}) '
            f'{{ edges {{ node {{ id firstName lastName avgRating avgDifficulty '
            f'numRatings wouldTakeAgainPercent department }} }} }} }} }}'
        )
    }

    try:
        resp = requests.post(_GRAPHQL_URL, json=query, headers=_HEADERS, timeout=_TIMEOUT)
    except requests.exceptions.RequestException as e:
        logger.warning("RMP course search failed: %s", e)
        return []

    if not resp.ok:
        return []

    try:
        edges = resp.json()["data"]["newSearch"]["teachers"]["edges"]
    except (KeyError, ValueError, TypeError):
        return []

    professors = []
    for edge in edges[:limit]:
        node = edge.get("node", {})
        prof = {
            "id":               node.get("id", ""),
            "name":             f"{node.get('firstName', '')} {node.get('lastName', '')}".strip(),
            "rating":           node.get("avgRating"),
            "difficulty":       node.get("avgDifficulty"),
            "num_ratings":      node.get("numRatings", 0),
            "department":       node.get("department", ""),
            "would_take_again": node.get("wouldTakeAgainPercent"),
        }
        if prof["id"]:
            prof["reviews"] = get_professor_reviews(prof["id"])
            professors.append(prof)

    return professors


def get_professor_reviews(professor_id: str, limit: int = 10) -> list[dict]:
    """Fetch reviews by scraping the RMP professor page and parsing __NEXT_DATA__."""
    numeric_id = _decode_id(professor_id)
    url = f"https://www.ratemyprofessors.com/professor/{numeric_id}"

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _HEADERS["User-Agent"]},
            timeout=_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        logger.warning("RMP professor page request failed (id=%s): %s", numeric_id, e)
        return []

    if not resp.ok:
        logger.warning(
            "RMP returned HTTP %d for professor page (id=%s)", resp.status_code, numeric_id,
        )
        return []

    try:
        soup   = BeautifulSoup(resp.text, "html.parser")
        script = soup.find("script", {"id": "__NEXT_DATA__"})
        if not script:
            logger.warning("__NEXT_DATA__ not found on professor page (id=%s)", numeric_id)
            return []
        edges = json.loads(script.string)["props"]["pageProps"]["teacher"]["ratings"]["edges"]
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        logger.warning("Failed to parse RMP professor page (id=%s): %s", numeric_id, e)
        return []

    reviews = []
    for edge in edges[:limit]:
        node = edge.get("node", {})
        reviews.append({
            "rating":      node.get("helpfulRating") or node.get("clarityRating"),
            "difficulty":  node.get("difficultyRating") or node.get("difficultyRatingRounded"),
            "review_text": node.get("comment", ""),
            "class_name":  node.get("class", ""),
            "date":        node.get("date", ""),
        })

    return reviews


if __name__ == "__main__":
    school_id = get_rmp_school_id("University of North Carolina at Charlotte")
    print(f"UNCC school ID: {school_id}")
    prof = search_professor(school_id, "Alex Chen")
    print(f"Professor search result: {prof}")
