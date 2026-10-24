"""Transparent scoring helpers for Campus Connect.

Scores are deterministic, configurable prototype heuristics, not validated predictions
of a person's future performance. Compatibility scores are only returned to an
authorized team's captain through the matching workflow.
"""
from __future__ import annotations
import json
import math
from typing import Any

PROFILE_WEIGHTS = {
    "skills_projects": 0.35,
    "hackathon_history": 0.25,
    "github_evidence": 0.20,
    "teammate_reviews": 0.20,
}
COMPATIBILITY_WEIGHTS = {
    "overall_profile": 0.25,
    "skill_gap_fit": 0.35,
    "availability_logistics": 0.20,
    "shared_goals": 0.10,
    "working_style": 0.10,
}


def loads(value: Any, default: Any = None) -> Any:
    if default is None:
        default = []
    if not value:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def _as_list(value: Any) -> list:
    parsed = loads(value, [])
    return parsed if isinstance(parsed, list) else []


def _text_list(value: Any) -> list[str]:
    return [str(x).strip() for x in _as_list(value) if str(x).strip()]


def _github_component(user: dict) -> tuple[float, str, int]:
    snapshot = loads(user.get("github_snapshot"), {})
    if isinstance(snapshot, dict) and snapshot.get("synced_at"):
        repos = snapshot.get("repos") or []
        count = int(snapshot.get("public_repos") or len(repos))
        active = int(snapshot.get("recent_repos") or 0)
        described = int(snapshot.get("described_repos") or 0)
        # Repository count is deliberately capped; stars/followers are not used as proxies for skill.
        score = 50 + min(20, count * 1.5) + min(15, active * 2) + min(10, described * 2)
        score = round(_clamp(score), 1)
        return score, f"Synced public GitHub data: {count} public repos, {active} recently updated, {described} with descriptions.", 85 if count else 60
    username = (user.get("github_username") or "").strip()
    if username:
        return 53.0, "GitHub username supplied; public activity has not been synced yet.", 20
    if user.get("github_url"):
        return 51.0, "GitHub profile link supplied, but repository evidence has not been retrieved.", 15
    return 50.0, "No GitHub evidence connected yet; treated as unknown rather than zero skill.", 0


def calculate_profile_score(conn, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise ValueError("User not found")
    user = dict(row)
    skills = _text_list(user.get("skills"))
    projects = _as_list(user.get("projects"))
    hackathons = _as_list(user.get("hackathons"))

    linked_projects = sum(1 for p in projects if isinstance(p, dict) and p.get("url"))
    supported_projects = sum(1 for p in projects if isinstance(p, dict) and p.get("evidence_status") in ("evidence_supported", "verified"))
    verified_projects = sum(1 for p in projects if isinstance(p, dict) and p.get("evidence_status") == "verified")
    # Skill/project component: self-declared skills are a useful starting signal, not proof.
    skill_score = 48 + min(22, len(skills) * 3.2) + min(18, len(projects) * 4.0) + min(10, linked_projects * 3.0) + min(8, supported_projects * 4.0) + min(5, verified_projects * 2.0)
    skill_score = round(_clamp(skill_score), 1)
    skill_evidence = f"{len(skills)} declared skills, {len(projects)} project entries, {linked_projects} project links; project links are not proof of authorship."
    skill_confidence = 12 if not skills and not projects else (40 if skills and not projects else (65 if linked_projects else 48))
    if supported_projects:
        skill_confidence = min(90, skill_confidence + 12)

    # Self-reported hackathon history is shown as claimed until independently corroborated.
    claimed_hackathons = len(hackathons)
    verified_hackathons = sum(1 for h in hackathons if isinstance(h, dict) and h.get("verification_status") == "verified")
    placements = sum(1 for h in hackathons if isinstance(h, dict) and h.get("outcome") and any(k in str(h.get("outcome", "")).lower() for k in ("winner", "1st", "2nd", "3rd", "finalist", "top ")))
    hack_score = 52 + min(18, claimed_hackathons * 5) + min(20, placements * 6) + min(10, verified_hackathons * 5)
    hack_score = round(_clamp(hack_score), 1)
    hack_evidence = f"{claimed_hackathons} listed hackathon experience item(s); {verified_hackathons} marked as independently verified. User-entered history remains self-reported unless corroborated."
    hack_confidence = 0 if claimed_hackathons == 0 else (35 if verified_hackathons == 0 else 75)

    github_score, github_evidence, github_confidence = _github_component(user)

    review_rows = conn.execute(
        "SELECT contribution,reliability,teamwork,communication,role_fulfilment FROM reviews WHERE target_id=? AND status='active'",
        (user_id,),
    ).fetchall()
    if review_rows:
        review_means = [sum(int(r[k]) for r in review_rows) / len(review_rows) for k in ("contribution", "reliability", "teamwork", "communication", "role_fulfilment")]
        review_score = round(_clamp((sum(review_means) / len(review_means)) * 20), 1)
        review_evidence = f"Based on {len(review_rows)} eligible post-collaboration review(s). Only confirmed teammates can submit reviews."
        review_confidence = min(90, 35 + len(review_rows) * 12)
    else:
        review_score = 55.0
        review_evidence = "No eligible teammate reviews yet; reliability is unknown, not assumed poor."
        review_confidence = 0

    components = [
        {"key": "skills_projects", "label": "Skills & project evidence", "weight": 35, "score": skill_score, "evidence": skill_evidence, "confidence": skill_confidence},
        {"key": "hackathon_history", "label": "Hackathon history", "weight": 25, "score": hack_score, "evidence": hack_evidence, "confidence": hack_confidence},
        {"key": "github_evidence", "label": "GitHub & technical portfolio", "weight": 20, "score": github_score, "evidence": github_evidence, "confidence": github_confidence},
        {"key": "teammate_reviews", "label": "Teammate reviews", "weight": 20, "score": review_score, "evidence": review_evidence, "confidence": review_confidence},
    ]
    overall = round(sum(c["score"] * c["weight"] / 100 for c in components))
    # Confidence reflects evidence coverage, not ability. Missing information has a neutral baseline score.
    confidence = round(sum(c["confidence"] * c["weight"] / 100 for c in components))
    confidence_label = "Strong evidence coverage" if confidence >= 65 else ("Some evidence available" if confidence >= 35 else "Early profile · more evidence needed")
    return {
        "score": int(_clamp(overall)),
        "label": "Overall Profile Score",
        "description": "An evidence-informed summary of skills, projects, participation and eligible teammate feedback. It is not a guarantee of future performance.",
        "confidence": confidence,
        "confidence_label": confidence_label,
        "components": components,
        "method": "Prototype heuristic · configurable weights · missing evidence is treated as unknown rather than zero.",
    }


def _token_set(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        text = " ".join(str(v) for v in value)
    else:
        text = str(value or "")
    import re
    return {t.lower() for t in re.findall(r"[a-zA-Z0-9+#.]{2,}", text)}


def calculate_compatibility(conn, candidate_id: int, team_id: int) -> dict:
    """Calculate a private, team-specific score for a captain's matching workflow only."""
    team_row = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
    if not team_row:
        raise ValueError("Team not found")
    team = dict(team_row)
    event_row = conn.execute("SELECT * FROM events WHERE id=?", (team["event_id"],)).fetchone()
    if not event_row:
        raise ValueError("Event not found")
    event = dict(event_row)
    candidate_row = conn.execute("SELECT * FROM users WHERE id=?", (candidate_id,)).fetchone()
    if not candidate_row:
        raise ValueError("Candidate not found")
    candidate = dict(candidate_row)
    profile = calculate_profile_score(conn, candidate_id)
    candidate_skills = {s.lower() for s in _text_list(candidate.get("skills"))}
    required = [str(s) for s in loads(event.get("required_skills"), [])]
    member_rows = conn.execute(
        "SELECT u.skills,u.interests,u.availability,u.work_preferences,u.profile_goal FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.team_id=? AND m.status='active' AND m.role='primary'",
        (team_id,),
    ).fetchall()
    current_skills: set[str] = set()
    team_interests: set[str] = set()
    team_availability: set[str] = set()
    team_work: set[str] = set()
    team_goals: set[str] = set()
    for r in member_rows:
        current_skills |= {s.lower() for s in _text_list(r["skills"])}
        team_interests |= _token_set(loads(r["interests"], []))
        team_availability |= _token_set(loads(r["availability"], []))
        team_work |= _token_set(loads(r["work_preferences"], []))
        team_goals |= _token_set(r["profile_goal"])
    missing = [skill for skill in required if skill.lower() not in current_skills]
    matched_missing = [skill for skill in missing if any(skill.lower() in s or s in skill.lower() for s in candidate_skills)]
    if missing:
        gap_score = round(45 + 55 * len(matched_missing) / max(1, len(missing)))
        if any(skill.lower() in s or s in skill.lower() for skill in required for s in candidate_skills):
            gap_score = max(gap_score, 58)
    else:
        # If skills are already covered, complementarity is less decisive, not a penalty by itself.
        candidate_unique = candidate_skills - current_skills
        gap_score = round(62 + min(25, len(candidate_unique) * 4))
    gap_score = int(_clamp(gap_score))

    candidate_avail = _token_set(loads(candidate.get("availability"), []))
    event_start = str(event.get("event_start") or "")
    event_month = event_start[5:7]
    weekend_event = False
    try:
        from datetime import date
        weekend_event = date.fromisoformat(event_start).weekday() >= 5
    except Exception:
        pass
    weekend_available = bool(candidate_avail & {"weekend", "weekends", "saturday", "sunday", "sat", "sun"})
    if weekend_event and weekend_available:
        logistics_score = 92
    elif candidate_avail and team_availability and candidate_avail & team_availability:
        logistics_score = 84
    elif not candidate_avail:
        logistics_score = 58
    else:
        logistics_score = 66
    if candidate.get("city") and team.get("location") and candidate.get("city", "").lower() == team.get("location", "").lower():
        logistics_score = min(100, logistics_score + 5)

    candidate_interests = _token_set(loads(candidate.get("interests"), []))
    event_text = " ".join([event.get("name", ""), event.get("description", ""), event.get("category", ""), " ".join(required)])
    event_terms = _token_set(event_text)
    overlap_event = len(candidate_interests & event_terms)
    overlap_team = len(candidate_interests & team_interests)
    goals_score = int(_clamp(55 + overlap_event * 5 + overlap_team * 4))

    candidate_work = _token_set(loads(candidate.get("work_preferences"), []))
    if candidate_work and team_work:
        union = candidate_work | team_work
        overlap = len(candidate_work & team_work) / max(1, len(union))
        work_score = round(55 + overlap * 45)
    else:
        work_score = 60

    components = [
        {"key": "overall_profile", "label": "Overall Profile Score", "weight": 25, "score": profile["score"], "explanation": "General capability and evidence score; does not include raw deserter allegations."},
        {"key": "skill_gap_fit", "label": "Missing-skill contribution", "weight": 35, "score": gap_score, "explanation": f"Could directly cover: {', '.join(matched_missing) if matched_missing else 'no exact missing required skill detected'}; remaining gaps: {', '.join([s for s in missing if s not in matched_missing]) if any(s not in matched_missing for s in missing) else 'none'}"},
        {"key": "availability_logistics", "label": "Availability & logistics", "weight": 20, "score": int(logistics_score), "explanation": "Estimated from the candidate's stated availability and the event/team location. Confirm with the candidate before finalising."},
        {"key": "shared_goals", "label": "Shared goals & interests", "weight": 10, "score": goals_score, "explanation": f"Based on relevant overlap between stated interests and this event ({overlap_event} event-related overlap signal(s))."},
        {"key": "working_style", "label": "Working preferences", "weight": 10, "score": int(work_score), "explanation": "Based on overlap in stated work preferences; missing information is treated as neutral."},
    ]
    score = round(sum(c["score"] * c["weight"] / 100 for c in components))
    # Pending cases are warnings only. Only independently upheld cases get a bounded, explicit adjustment.
    upheld = conn.execute(
        "SELECT COUNT(*) FROM deserter_cases WHERE target_id=? AND status='upheld'",
        (candidate_id,),
    ).fetchone()[0]
    adjustment = -min(8, int(upheld) * 4) if upheld else 0
    score = int(_clamp(score + adjustment))
    possible_cases = conn.execute(
        "SELECT event_name,status,created_at FROM deserter_cases WHERE target_id=? AND status IN ('pending','disputed','upheld') ORDER BY created_at DESC",
        (candidate_id,),
    ).fetchall()
    warnings = []
    for case in possible_cases:
        status = case["status"]
        warnings.append({"label": "Possible Deserter" if status in ("pending", "disputed") else "Reviewed no-show history", "status": status, "event": case["event_name"], "explanation": "A report is not proof. Review its status and context; pending or disputed reports do not alter the numeric score." if status in ("pending", "disputed") else "An authorised review upheld a no-show incident; a small, capped reliability adjustment is applied to this captain-only match score."})
    return {
        "score": score,
        "label": "Team Compatibility Score",
        "scope": "Private to the captain's matching workflow; not displayed on the candidate's profile or join flow.",
        "components": components,
        "missing_skills": missing,
        "matched_missing_skills": matched_missing,
        "reliability_adjustment": adjustment,
        "warnings": warnings,
        "explanation": f"Compatibility for {event.get('name')} and team {team.get('name')}; this is team-specific, not a global rating.",
    }
