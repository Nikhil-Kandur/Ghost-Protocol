from __future__ import annotations
import hashlib
import json
import os
import re
import secrets
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agents import llm_configured, llm_provider, log_event, run_team_architect, log_simple_run
from .db import connect, dumps, hash_password, init_db, now_iso, verify_password
from .scoring import calculate_compatibility, calculate_profile_score, loads

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_COOKIE = "cc_session"
SESSION_DAYS = int(os.getenv("CAMPUS_SESSION_DAYS", "7"))
DEMO_MODE = os.getenv("CAMPUS_DEMO_MODE", "true").lower() in ("1", "true", "yes")
COOKIE_SECURE = os.getenv("CAMPUS_COOKIE_SECURE", "false").lower() in ("1", "true", "yes")

app = FastAPI(title="Campus Connect", version="1.0.0", description="Agentic event discovery and team formation platform")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
init_db()


# ------------------------- request models -------------------------
class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    college: str = Field(default="", max_length=120)
    city: str = Field(default="Chennai", max_length=100)

class LoginIn(BaseModel):
    email: str
    password: str

class ProfileIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=80)
    college: Optional[str] = Field(default=None, max_length=120)
    course: Optional[str] = Field(default=None, max_length=100)
    year: Optional[str] = Field(default=None, max_length=50)
    bio: Optional[str] = Field(default=None, max_length=500)
    city: Optional[str] = Field(default=None, max_length=100)
    skills: Optional[list[str]] = None
    interests: Optional[list[str]] = None
    roles: Optional[list[str]] = None
    availability: Optional[list[str]] = None
    work_preferences: Optional[list[str]] = None
    profile_goal: Optional[str] = Field(default=None, max_length=500)
    github_username: Optional[str] = Field(default=None, max_length=39)
    github_url: Optional[str] = Field(default=None, max_length=300)
    linkedin_url: Optional[str] = Field(default=None, max_length=300)
    portfolio_url: Optional[str] = Field(default=None, max_length=300)
    projects: Optional[list[dict]] = None
    hackathons: Optional[list[dict]] = None
    discoverable: Optional[bool] = None
    preferred_mode: Optional[str] = Field(default=None, max_length=30)
    travel_radius: Optional[int] = Field(default=None, ge=0, le=1000)
    notification_preference: Optional[str] = Field(default=None, pattern="^(in_app|in_app_email)$")

class CreateTeamIn(BaseModel):
    event_id: int
    name: str = Field(min_length=2, max_length=100)
    goal: str = Field(default="", max_length=500)

class MatchIn(BaseModel):
    target_role: str = Field(default="primary", pattern="^(primary|backup)$")
    recovery: bool = False
    goal: Optional[str] = Field(default=None, max_length=300)

class RequestIn(BaseModel):
    candidate_id: int
    role: str = Field(pattern="^(primary|backup)$")
    role_label: str = Field(default="", max_length=80)
    message: str = Field(default="", max_length=500)

class BulkRequestIn(BaseModel):
    candidate_ids: list[int] = Field(min_length=1, max_length=8)
    run_id: int = Field(gt=0)
    role: str = Field(pattern="^(primary|backup)$")
    message: str = Field(default="Campus Connect recommends you for this team. Please review the event and accept only if you can commit.", max_length=500)

class AdminPersonIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    college: str = Field(default="", max_length=120)
    course: str = Field(default="", max_length=100)
    year: str = Field(default="", max_length=50)
    city: str = Field(default="Chennai", max_length=100)
    bio: str = Field(default="", max_length=500)
    profile_goal: str = Field(default="", max_length=500)
    skills: list[str] = Field(default_factory=list, max_length=30)
    interests: list[str] = Field(default_factory=list, max_length=30)
    roles: list[str] = Field(default_factory=list, max_length=15)
    availability: list[str] = Field(default_factory=lambda: ["weekends"], max_length=20)
    work_preferences: list[str] = Field(default_factory=lambda: ["collaborative"], max_length=20)
    github_username: str = Field(default="", max_length=39)
    github_url: str = Field(default="", max_length=300)
    linkedin_url: str = Field(default="", max_length=300)
    portfolio_url: str = Field(default="", max_length=300)
    projects: list[dict] = Field(default_factory=list, max_length=30)
    hackathons: list[dict] = Field(default_factory=list, max_length=30)
    discoverable: bool = True

class ApplyIn(BaseModel):
    role: str = Field(pattern="^(primary|backup)$")
    role_label: str = Field(default="", max_length=80)
    message: str = Field(default="", max_length=500)

class RespondIn(BaseModel):
    action: str = Field(pattern="^(accept|decline)$")

class WithdrawIn(BaseModel):
    reason: str = Field(default="", max_length=300)

class ReviewIn(BaseModel):
    target_id: int
    contribution: int = Field(ge=1, le=5)
    reliability: int = Field(ge=1, le=5)
    teamwork: int = Field(ge=1, le=5)
    communication: int = Field(ge=1, le=5)
    role_fulfilment: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=1000)
    report_deserter: bool = False
    desertion_details: str = Field(default="", max_length=1000)

class DeserterReportIn(BaseModel):
    target_id: int
    explanation: str = Field(min_length=10, max_length=1000)

class AppealIn(BaseModel):
    response: str = Field(min_length=10, max_length=1500)

class AdminCaseReviewIn(BaseModel):
    decision: str = Field(pattern="^(uphold|dismiss|review)$")
    note: str = Field(default="", max_length=1500)

class EventIn(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=2000)
    organizer: str = Field(default="", max_length=160)
    category: str = Field(default="Hackathon", max_length=80)
    mode: str = Field(default="In person", max_length=40)
    location: str = Field(default="Chennai", max_length=120)
    event_start: str
    event_end: str
    registration_deadline: str
    min_team_size: int = Field(default=2, ge=1, le=20)
    max_team_size: int = Field(default=4, ge=1, le=20)
    required_skills: list[str] = []
    eligibility: str = Field(default="Open to college students", max_length=500)
    registration_url: str = Field(default="", max_length=500)
    source_url: str = Field(default="", max_length=500)
    source_label: str = Field(default="Admin supplied", max_length=100)
    status: str = Field(default="upcoming", pattern="^(upcoming|registration_open|closed|ongoing|completed|cancelled)$")

class EventInterestIn(BaseModel):
    interested: bool = True
    saved: Optional[bool] = None

# ------------------------- helper functions -------------------------
def safe_json(value: Any, default=None):
    if default is None:
        default = []
    return loads(value, default)


def _iso_after_days(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat()


def _session_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def current_user(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    conn = connect()
    try:
        row = conn.execute("SELECT s.expires_at,u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (_session_hash(token),)).fetchone()
        if not row or row["expires_at"] <= now_iso():
            if row:
                conn.execute("DELETE FROM sessions WHERE token_hash=?", (_session_hash(token),))
                conn.commit()
            raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
        return dict(row)
    finally:
        conn.close()


def _require_captain(conn, team_id: int, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Team not found.")
    team = dict(row)
    if team["captain_id"] != user_id:
        raise HTTPException(status_code=403, detail="Only the team captain can perform this action.")
    return team


def _event(conn, event_id: int) -> dict:
    row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Event not found.")
    return dict(row)


def _active_members(conn, team_id: int, role: str | None = None) -> list[dict]:
    sql = "SELECT m.*,u.name,u.email,u.city,u.skills,u.roles,u.bio,u.is_demo FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.team_id=? AND m.status='active'"
    params: list[Any] = [team_id]
    if role:
        sql += " AND m.role=?"
        params.append(role)
    sql += " ORDER BY CASE m.role WHEN 'primary' THEN 0 ELSE 1 END, m.joined_at"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _counts(conn, team: dict) -> dict:
    members = _active_members(conn, team["id"])
    primary = [m for m in members if m["role"] == "primary"]
    backup = [m for m in members if m["role"] == "backup"]
    pending_primary = conn.execute("SELECT COUNT(*) FROM requests WHERE team_id=? AND status='pending' AND offered_role='primary'", (team["id"],)).fetchone()[0]
    pending_backup = conn.execute("SELECT COUNT(*) FROM requests WHERE team_id=? AND status='pending' AND offered_role='backup'", (team["id"],)).fetchone()[0]
    return {"primary_count": len(primary), "backup_count": len(backup), "primary_limit": int(team["primary_limit"]), "primary_slots_open": max(0, int(team["primary_limit"]) - len(primary)), "primary_slots_available_to_request": max(0, int(team["primary_limit"]) - len(primary) - int(pending_primary)), "backup_slot_available": len(backup) == 0 and int(pending_backup) == 0, "pending_primary_requests": int(pending_primary), "pending_backup_requests": int(pending_backup)}


def _active_deserter_tags(conn, user_id: int) -> list[dict]:
    rows = conn.execute("SELECT id,event_id,event_name,report_count,required_threshold,status,created_at,response_text FROM deserter_cases WHERE target_id=? AND status IN ('pending','disputed','upheld') ORDER BY created_at DESC", (user_id,)).fetchall()
    return [{"case_id": r["id"], "event_id": r["event_id"], "event": r["event_name"], "report_count": r["report_count"], "required_threshold": r["required_threshold"], "status": r["status"], "created_at": r["created_at"], "has_responded": bool(r["response_text"]), "label": "Possible Deserter" if r["status"] in ("pending", "disputed") else "Reviewed no-show history", "explanation": "A multi-reporter warning, not proof of deliberate desertion." if r["status"] in ("pending", "disputed") else "An authorised moderator upheld a no-show incident."} for r in rows]


def _profile_dict(conn, user_id: int, *, include_private=False) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found.")
    u = dict(row)
    profile = calculate_profile_score(conn, user_id)
    review_summary = conn.execute("SELECT COUNT(*) AS n, AVG((contribution+reliability+teamwork+communication+role_fulfilment)/5.0) AS avg FROM reviews WHERE target_id=? AND status='active'", (user_id,)).fetchone()
    result = {
        "id": u["id"], "name": u["name"], "college": u["college"], "course": u["course"], "year": u["year"],
        "bio": u["bio"], "city": u["city"], "skills": safe_json(u["skills"]), "interests": safe_json(u["interests"]),
        "roles": safe_json(u["roles"]), "availability": safe_json(u["availability"]), "work_preferences": safe_json(u["work_preferences"]),
        "profile_goal": u["profile_goal"], "github_username": u["github_username"], "github_url": u["github_url"],
        "linkedin_url": u["linkedin_url"], "portfolio_url": u["portfolio_url"], "projects": safe_json(u["projects"]),
        "hackathons": safe_json(u["hackathons"]), "discoverable": bool(u["discoverable"]), "preferred_mode": u["preferred_mode"],
        "travel_radius": u["travel_radius"], "is_demo": bool(u["is_demo"]), "is_admin": bool(u["is_admin"]),
        "notification_preference": u.get("notification_preference", "in_app"),
        "email_delivery_configured": _smtp_configured(),
        "overall_profile_score": profile, "deserter_tags": _active_deserter_tags(conn, user_id),
        "review_count": int(review_summary["n"] or 0), "review_average": round(float(review_summary["avg"] or 0), 2) if review_summary["n"] else None,
        "github_synced": bool(safe_json(u["github_snapshot"], {}).get("synced_at")),
    }
    if include_private:
        result["email"] = u["email"]
        result["created_at"] = u["created_at"]
    # Intentionally no Team Compatibility Score here. Compatibility is private to captain matching.
    return result


def _event_dict(conn, event_row, user_id: int | None = None) -> dict:
    e = dict(event_row)
    interest = False
    saved = False
    if user_id:
        interest = conn.execute("SELECT 1 FROM event_interactions WHERE user_id=? AND event_id=? AND kind='interested'", (user_id, e["id"])).fetchone() is not None
        saved = conn.execute("SELECT 1 FROM event_interactions WHERE user_id=? AND event_id=? AND kind='saved'", (user_id, e["id"])).fetchone() is not None
    return {"id": e["id"], "name": e["name"], "description": e["description"], "organizer": e["organizer"], "category": e["category"], "mode": e["mode"], "location": e["location"], "event_start": e["event_start"], "event_end": e["event_end"], "registration_deadline": e["registration_deadline"], "min_team_size": e["min_team_size"], "max_team_size": e["max_team_size"], "required_skills": safe_json(e["required_skills"]), "eligibility": e["eligibility"], "registration_url": e["registration_url"], "source_url": e["source_url"], "source_label": e["source_label"], "last_verified_at": e["last_verified_at"], "status": e["status"], "is_demo": bool(e["is_demo"]), "interested": interest, "saved": saved}


def _team_card(conn, team_row, viewer_id: int) -> dict:
    team = dict(team_row)
    event = conn.execute("SELECT * FROM events WHERE id=?", (team["event_id"],)).fetchone()
    captain = conn.execute("SELECT id,name,city,is_demo FROM users WHERE id=?", (team["captain_id"],)).fetchone()
    counts = _counts(conn, team)
    members = _active_members(conn, team["id"])
    pending = conn.execute("SELECT COUNT(*) FROM requests WHERE team_id=? AND status='pending'", (team["id"],)).fetchone()[0] if team["captain_id"] == viewer_id else None
    return {"id": team["id"], "name": team["name"], "event_id": team["event_id"], "event_name": event["name"] if event else "Unknown event", "event_status": event["status"] if event else "unknown", "event_start": event["event_start"] if event else "", "location": event["location"] if event else "", "mode": event["mode"] if event else "", "goal": team["goal"], "captain_id": team["captain_id"], "captain_name": captain["name"] if captain else "Unknown", "status": team["status"], **counts, "backup": next(({"user_id":m["user_id"],"name":m["name"],"role_label":m["role_label"]} for m in members if m["role"] == "backup"), None), "members": [{"id":m["user_id"],"name":m["name"],"role":m["role"],"role_label":m["role_label"],"city":m["city"],"skills":safe_json(m["skills"]),"is_demo":bool(m["is_demo"])} for m in members], "pending_requests": pending, "is_captain": team["captain_id"] == viewer_id, "is_member": any(m["user_id"] == viewer_id for m in members)}


def _smtp_configured() -> bool:
    return bool(os.getenv("CAMPUS_SMTP_HOST", "").strip() and os.getenv("CAMPUS_SMTP_FROM", os.getenv("CAMPUS_SMTP_USERNAME", "")).strip())


def _send_notification_email(recipient_id: int, title: str, body: str) -> str:
    """Best-effort SMTP delivery; inbox persistence does not depend on email success."""
    host = os.getenv("CAMPUS_SMTP_HOST", "").strip()
    sender = os.getenv("CAMPUS_SMTP_FROM", os.getenv("CAMPUS_SMTP_USERNAME", "")).strip()
    if not host or not sender:
        return "not_configured"
    conn = connect()
    try:
        recipient = conn.execute("SELECT email,is_demo FROM users WHERE id=?", (recipient_id,)).fetchone()
        if not recipient:
            return "recipient_missing"
        if recipient["is_demo"]:
            return "demo_account_skipped"
        address = recipient["email"]
    finally:
        conn.close()
    msg = EmailMessage()
    msg["Subject"] = f"Campus Connect: {title}"
    msg["From"] = sender
    msg["To"] = address
    msg.set_content(f"{title}\n\n{body}\n\nOpen Campus Connect to review details and take any available action.")
    try:
        port = int(os.getenv("CAMPUS_SMTP_PORT", "587"))
    except ValueError:
        return "failed"
    username = os.getenv("CAMPUS_SMTP_USERNAME", "").strip()
    password = os.getenv("CAMPUS_SMTP_PASSWORD", "")
    use_starttls = os.getenv("CAMPUS_SMTP_STARTTLS", "true").lower() in ("1", "true", "yes")
    try:
        timeout = float(os.getenv("CAMPUS_SMTP_TIMEOUT", "4"))
    except ValueError:
        timeout = 4.0
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=timeout) as server:
                if username:
                    server.login(username, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as server:
                server.ehlo()
                if use_starttls:
                    server.starttls()
                    server.ehlo()
                if username:
                    server.login(username, password)
                server.send_message(msg)
        return "sent"
    except (OSError, smtplib.SMTPException, ValueError):
        return "failed"


def _message(conn, recipient_id: int, title: str, body: str, *, msg_type="system", sender_id=None, data=None):
    payload = dict(data or {})
    pref_row = conn.execute("SELECT notification_preference FROM users WHERE id=?", (recipient_id,)).fetchone()
    preference = pref_row["notification_preference"] if pref_row else "in_app"
    if preference == "in_app_email":
        delivery = _send_notification_email(recipient_id, title, body)
        payload["email_delivery_status"] = delivery
    conn.execute("INSERT INTO messages(recipient_id,sender_id,type,title,body,data,is_read,created_at) VALUES(?,?,?,?,?,?,0,?)", (recipient_id, sender_id, msg_type, title, body, dumps(payload), now_iso()))


def _log(conn, team_id: int | None, actor_id: int | None, agent: str, action: str, details: dict | None = None):
    log_event(conn, team_id=team_id, actor_id=actor_id, agent=agent, action=action, details=details or {})


def _check_open_event(conn, event_id: int) -> dict:
    event = _event(conn, event_id)
    if event["status"] in ("completed", "cancelled", "closed"):
        raise HTTPException(status_code=400, detail="This event is not accepting new team participation.")
    if event["registration_deadline"] and event["registration_deadline"] < datetime.now(timezone.utc).date().isoformat():
        raise HTTPException(status_code=400, detail="The registration deadline has passed.")
    return event


def _active_commitment(conn, user_id: int, event_id: int, exclude_team: int | None = None) -> bool:
    sql = "SELECT 1 FROM memberships m JOIN teams t ON t.id=m.team_id WHERE m.user_id=? AND m.status='active' AND t.event_id=? AND t.status IN ('recruiting','active','recovery_needed')"
    params: list[Any] = [user_id, event_id]
    if exclude_team is not None:
        sql += " AND t.id<>?"
        params.append(exclude_team)
    return conn.execute(sql + " LIMIT 1", params).fetchone() is not None


def _has_block(conn, first: int, second: int) -> bool:
    return conn.execute("SELECT 1 FROM blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)", (first, second, second, first)).fetchone() is not None


def _notify_request_result(conn, request: dict, action: str):
    team = conn.execute("SELECT name,captain_id FROM teams WHERE id=?", (request["team_id"],)).fetchone()
    sender = conn.execute("SELECT name FROM users WHERE id=?", (request["to_user_id"],)).fetchone()
    actor_name = sender["name"] if sender else "A team member"
    if request["kind"] == "invite":
        recipient = request["from_user_id"]
        title = f"{actor_name} {action} your team invitation"
        body = f"{actor_name} {action} the invitation to join {team['name']} as {request['offered_role']}."
    elif request["kind"] == "application":
        recipient = request["from_user_id"]
        title = f"Your request to join {team['name']} was {action}"
        body = f"The captain {action} your request to join as {request['offered_role']}."
    else:
        recipient = request["from_user_id"]
        title = f"Backup promotion request {action}"
        body = f"Your backup promotion request for {team['name']} was {action}."
    _message(conn, recipient, title, body, msg_type="request_update", sender_id=request["to_user_id"], data={"team_id": request["team_id"], "request_id": request["id"], "action": action})


def _add_membership(conn, team_id: int, user_id: int, role: str, role_label: str = ""):
    row = conn.execute("SELECT id FROM memberships WHERE team_id=? AND user_id=?", (team_id, user_id)).fetchone()
    ts = now_iso()
    if row:
        conn.execute("UPDATE memberships SET role=?,status='active',role_label=?,joined_at=?,withdrawn_at='' WHERE id=?", (role, role_label, ts, row["id"]))
    else:
        conn.execute("INSERT INTO memberships(team_id,user_id,role,status,role_label,joined_at) VALUES(?,?,?,'active',?,?)", (team_id, user_id, role, role_label, ts))


def _get_request(conn, request_id: int) -> dict:
    row = conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Request not found.")
    return dict(row)


def _request_slots_check(conn, team: dict, role: str, *, request_id: int | None = None):
    counts = _counts(conn, team)
    if role == "primary":
        active = counts["primary_count"]
        if active >= int(team["primary_limit"]):
            raise HTTPException(status_code=409, detail="The primary team is already full.")
    elif role == "backup":
        active_backup = counts["backup_count"]
        if active_backup >= 1:
            raise HTTPException(status_code=409, detail="This team already has its one designated backup.")
    else:
        raise HTTPException(status_code=400, detail="Unsupported membership role.")


def _create_request(conn, *, team: dict, from_user_id: int, to_user_id: int, kind: str, role: str, role_label: str = "", message: str = "") -> int:
    existing = conn.execute("SELECT id FROM requests WHERE team_id=? AND from_user_id=? AND to_user_id=? AND kind=? AND offered_role=? AND status='pending'", (team["id"], from_user_id, to_user_id, kind, role)).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="A matching request is already pending.")
    event = _check_open_event(conn, team["event_id"])
    if from_user_id != team["captain_id"] and kind != "application":
        # Candidate-initiated membership actions must not impersonate the captain.
        raise HTTPException(status_code=403, detail="Only the captain can send this request.")
    if from_user_id != to_user_id and _has_block(conn, from_user_id, to_user_id):
        raise HTTPException(status_code=403, detail="This request cannot be sent because of a privacy or block setting.")
    membership = conn.execute("SELECT role,status FROM memberships WHERE team_id=? AND user_id=?", (team["id"], to_user_id if kind == "invite" else from_user_id)).fetchone()
    if membership and membership["status"] == "active":
        raise HTTPException(status_code=409, detail="This user is already a member of the team.")
    if kind in ("invite", "application"):
        # Check whether the intended target is already committed to a team for this same event.
        candidate_id = to_user_id if kind == "invite" else from_user_id
        if _active_commitment(conn, candidate_id, team["event_id"], exclude_team=team["id"]):
            raise HTTPException(status_code=409, detail="This student already has an active commitment for this event.")
        # Only one pending membership request per user/team and offered role.
        pending_for_user = conn.execute("SELECT 1 FROM requests r JOIN teams t ON t.id=r.team_id WHERE t.event_id=? AND r.status='pending' AND r.team_id<>? AND ((r.to_user_id=? AND r.kind='invite') OR (r.from_user_id=? AND r.kind='application')) LIMIT 1", (team["event_id"], team["id"], candidate_id, candidate_id)).fetchone()
        if pending_for_user:
            raise HTTPException(status_code=409, detail="This student already has a pending request for another team in the same event.")
    counts = _counts(conn, team)
    if role == "primary":
        if counts["primary_count"] + counts["pending_primary_requests"] >= int(team["primary_limit"]):
            raise HTTPException(status_code=409, detail="There are no unreserved primary slots left. Resolve pending invitations or increase the permitted team size only if event rules allow it.")
    elif role == "backup":
        if counts["backup_count"] + counts["pending_backup_requests"] >= 1:
            raise HTTPException(status_code=409, detail="The team already has a backup or a pending backup request.")
        if int(event["max_team_size"]) <= int(counts["primary_count"]):
            # Backup status is not automatically counted as an official primary; event-specific substitute rules still need verification.
            pass
    ts = now_iso()
    cur = conn.execute("INSERT INTO requests(team_id,from_user_id,to_user_id,kind,offered_role,role_label,message,status,created_at) VALUES(?,?,?,?,?,?,?,'pending',?)", (team["id"], from_user_id, to_user_id, kind, role, role_label, message, ts))
    request_id = cur.lastrowid
    conn.commit()
    return request_id


def _deserter_threshold(eligible_reporters: int) -> int | None:
    if eligible_reporters < 2:
        return None
    import math
    return max(2, math.ceil(eligible_reporters / 2))


def _record_deserter_report(conn, team_id: int, reporter_id: int, target_id: int, explanation: str) -> dict:
    team_row = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
    if not team_row:
        raise HTTPException(status_code=404, detail="Team not found.")
    team = dict(team_row)
    event = _event(conn, team["event_id"])
    if event["status"] != "completed" and team["status"] != "completed":
        raise HTTPException(status_code=400, detail="Desertion reports are available only after the event has completed.")
    if reporter_id == target_id:
        raise HTTPException(status_code=400, detail="You cannot report yourself.")
    reporter_mem = conn.execute("SELECT role,status FROM memberships WHERE team_id=? AND user_id=?", (team_id, reporter_id)).fetchone()
    target_mem = conn.execute("SELECT role,status FROM memberships WHERE team_id=? AND user_id=?", (team_id, target_id)).fetchone()
    if not reporter_mem or reporter_mem["role"] != "primary" or reporter_mem["status"] != "active":
        raise HTTPException(status_code=403, detail="Only a confirmed primary teammate can submit this report.")
    if not target_mem or target_mem["role"] != "primary" or target_mem["status"] != "active":
        raise HTTPException(status_code=400, detail="Only a currently recorded primary member can be reported for a potential event-day no-show. A member who withdrew from the team is not automatically a deserter.")
    existing = conn.execute("SELECT id FROM deserter_reports WHERE event_id=? AND reporter_id=? AND target_id=?", (event["id"], reporter_id, target_id)).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="You have already submitted a desertion report for this teammate and event.")
    ts = now_iso()
    conn.execute("INSERT INTO deserter_reports(team_id,event_id,reporter_id,target_id,explanation,status,created_at) VALUES(?,?,?,?,?,'valid',?)", (team_id, event["id"], reporter_id, target_id, explanation.strip(), ts))
    eligible = conn.execute("SELECT COUNT(*) FROM memberships WHERE team_id=? AND role='primary' AND status='active' AND user_id<>?", (team_id, target_id)).fetchone()[0]
    threshold = _deserter_threshold(int(eligible))
    count = conn.execute("SELECT COUNT(*) FROM deserter_reports WHERE team_id=? AND event_id=? AND target_id=? AND status='valid'", (team_id, event["id"], target_id)).fetchone()[0]
    case_row = conn.execute("SELECT * FROM deserter_cases WHERE team_id=? AND event_id=? AND target_id=?", (team_id, event["id"], target_id)).fetchone()
    if threshold is None:
        status = "under_review"
        required = 2
    else:
        status = "pending" if int(count) >= threshold else "under_review"
        required = threshold
    prior_status = case_row["status"] if case_row else None
    if case_row:
        case_id = case_row["id"]
        # Do not erase a moderator decision. New valid reports update counts, not a prior dismissal/upheld decision.
        prior_status = case_row["status"]
        if prior_status in ("upheld", "dismissed"):
            status = prior_status
        elif prior_status == "disputed":
            status = "disputed"
        conn.execute("UPDATE deserter_cases SET report_count=?,required_threshold=?,status=?,updated_at=? WHERE id=?", (int(count), required, status, ts, case_id))
    else:
        cur = conn.execute("INSERT INTO deserter_cases(team_id,event_id,target_id,event_name,report_count,required_threshold,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (team_id, event["id"], target_id, event["name"], int(count), required, status, ts, ts))
        case_id = cur.lastrowid
    just_reached_threshold = threshold is not None and int(count) >= threshold and status in ("pending", "disputed") and prior_status not in ("pending", "disputed", "upheld", "dismissed")
    if just_reached_threshold:
        _message(conn, target_id, "A Possible Deserter report needs your response", f"Multiple eligible teammates reported that you missed a commitment for {event['name']}. This is a warning, not a finding of deliberate misconduct. You can provide a response or dispute the report.", msg_type="deserter_case", sender_id=None, data={"case_id": case_id, "event_id": event["id"]})
        admin_rows = conn.execute("SELECT id FROM users WHERE is_admin=1").fetchall()
        for admin_row in admin_rows:
            _message(conn, admin_row["id"], "Deserter case reached reporting threshold", f"A possible no-show case reached its threshold for {event['name']}. Review the evidence and any response before deciding.", msg_type="admin_review", data={"case_id": case_id})
    _log(conn, team_id, reporter_id, "Trust & Review Agent", "Recorded eligible desertion report", {"target_id": target_id, "event_id": event["id"], "valid_report_count": int(count), "required_threshold": threshold, "case_status": status})
    conn.commit()
    return {"case_id": case_id, "report_count": int(count), "eligible_reporters": int(eligible), "required_threshold": threshold, "status": status, "tag_active": status in ("pending", "disputed", "upheld")}

# ------------------------- auth -------------------------
@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/api/health")
def health():
    provider = llm_provider()
    return {"ok": True, "product": "Campus Connect", "version": app.version, "agent_mode": "tool-using orchestrated workflows", "llm_configured": llm_configured(), "llm_provider": provider, "llm_status": f"{provider} tool-use review enabled; deterministic scoring and state changes remain authoritative" if llm_configured() else "No LLM API key configured; deterministic agent tools and recovery workflows remain functional", "email_delivery_configured": _smtp_configured(), "demo_mode": DEMO_MODE}

@app.post("/api/auth/register")
def register(data: RegisterIn, response: Response):
    email = data.email.strip().lower()
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    conn = connect()
    try:
        ts = now_iso()
        try:
            cur = conn.execute("INSERT INTO users(email,password_hash,name,college,city,discoverable,is_demo,created_at,updated_at) VALUES(?,?,?,?,?,1,0,?,?)", (email, hash_password(data.password), data.name.strip(), data.college.strip(), data.city.strip(), ts, ts))
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=409, detail="An account with this email already exists.")
            raise
        uid = cur.lastrowid
        raw = secrets.token_urlsafe(36)
        conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (_session_hash(raw), uid, _iso_after_days(SESSION_DAYS), ts))
        conn.commit()
        response.set_cookie(SESSION_COOKIE, raw, httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=SESSION_DAYS * 86400, path="/")
        _message(conn, uid, "Welcome to Campus Connect", "Complete your profile to improve event recommendations and team matching.", msg_type="system")
        conn.commit()
        return {"user": _profile_dict(conn, uid, include_private=True)}
    finally:
        conn.close()

@app.post("/api/auth/login")
def login(data: LoginIn, response: Response):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (data.email.strip().lower(),)).fetchone()
        if not row or not verify_password(data.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Email or password is incorrect.")
        raw = secrets.token_urlsafe(36)
        ts = now_iso()
        conn.execute("DELETE FROM sessions WHERE expires_at<?", (ts,))
        conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (_session_hash(raw), row["id"], _iso_after_days(SESSION_DAYS), ts))
        conn.commit()
        response.set_cookie(SESSION_COOKIE, raw, httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=SESSION_DAYS * 86400, path="/")
        return {"user": _profile_dict(conn, row["id"], include_private=True)}
    finally:
        conn.close()

@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        conn = connect()
        try:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (_session_hash(token),))
            conn.commit()
        finally:
            conn.close()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}

@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    conn = connect()
    try:
        return {"user": _profile_dict(conn, user["id"], include_private=True), "health": {"llm_configured": llm_configured(), "demo_mode": DEMO_MODE}}
    finally:
        conn.close()

@app.get("/api/demo-accounts")
def demo_accounts():
    if not DEMO_MODE:
        return {"enabled": False, "accounts": []}
    return {"enabled": True, "password": "Campus123!", "accounts": [
        {"email":"captain@campusconnect.demo","name":"Olivia · Team captain","role":"Captain demo"},
        {"email":"arjun@campusconnect.demo","name":"Arjun · Backend","role":"Candidate demo"},
        {"email":"karthik@campusconnect.demo","name":"Karthik · AI/ML","role":"Candidate demo"},
        {"email":"maya@campusconnect.demo","name":"Maya · Frontend","role":"Candidate demo"},
        {"email":"rhea@campusconnect.demo","name":"Rhea · Pitch / research","role":"Candidate demo"},
        {"email":"admin@campusconnect.demo","name":"Platform administrator","role":"Admin demo"},
    ]}

# ------------------------- dashboard / profiles -------------------------
@app.get("/api/dashboard")
def dashboard(user: dict = Depends(current_user)):
    conn = connect()
    try:
        uid = user["id"]
        events = conn.execute("SELECT * FROM events WHERE status IN ('upcoming','registration_open','ongoing') ORDER BY event_start LIMIT 6").fetchall()
        team_rows = conn.execute("SELECT DISTINCT t.* FROM teams t LEFT JOIN memberships m ON m.team_id=t.id AND m.status='active' WHERE (m.user_id=? OR t.captain_id=?) ORDER BY CASE t.status WHEN 'recruiting' THEN 0 ELSE 1 END,t.updated_at DESC LIMIT 6", (uid, uid)).fetchall()
        unread = conn.execute("SELECT COUNT(*) FROM messages WHERE recipient_id=? AND is_read=0", (uid,)).fetchone()[0]
        inbox = conn.execute("SELECT * FROM messages WHERE recipient_id=? ORDER BY created_at DESC LIMIT 6", (uid,)).fetchall()
        activity = conn.execute("SELECT ae.* FROM agent_events ae LEFT JOIN memberships m ON m.team_id=ae.team_id AND m.user_id=? AND m.status='active' LEFT JOIN teams t ON t.id=ae.team_id WHERE (ae.actor_id=? OR m.user_id=? OR t.captain_id=?) GROUP BY ae.id ORDER BY ae.id DESC LIMIT 8", (uid, uid, uid, uid)).fetchall()
        return {"profile": _profile_dict(conn, uid), "events": [_event_dict(conn, e, uid) for e in events], "teams": [_team_card(conn, t, uid) for t in team_rows], "unread_count": int(unread), "recent_messages": [_message_dict(conn, m) for m in inbox], "agent_activity": [_agent_event_dict(a) for a in activity], "stats": {"events": int(conn.execute("SELECT COUNT(*) FROM events WHERE status IN ('upcoming','registration_open','ongoing')").fetchone()[0]), "teams": int(conn.execute("SELECT COUNT(DISTINCT t.id) FROM teams t LEFT JOIN memberships m ON m.team_id=t.id AND m.status='active' WHERE (m.user_id=? OR t.captain_id=?)", (uid, uid)).fetchone()[0]), "pending_requests": int(conn.execute("SELECT COUNT(*) FROM requests WHERE to_user_id=? AND status='pending'", (uid,)).fetchone()[0])}}
    finally:
        conn.close()


def _message_dict(conn, row) -> dict:
    m = dict(row)
    sender_name = None
    if m.get("sender_id"):
        sr = conn.execute("SELECT name FROM users WHERE id=?", (m["sender_id"],)).fetchone()
        sender_name = sr["name"] if sr else None
    return {"id": m["id"], "type": m["type"], "title": m["title"], "body": m["body"], "data": safe_json(m["data"], {}), "is_read": bool(m["is_read"]), "created_at": m["created_at"], "sender_id": m["sender_id"], "sender_name": sender_name}


def _agent_event_dict(row) -> dict:
    r = dict(row)
    return {"id": r["id"], "run_id": r["run_id"], "team_id": r["team_id"], "agent": r["agent"], "action": r["action"], "details": safe_json(r["details"], {}), "created_at": r["created_at"]}

@app.patch("/api/profile")
def update_profile(data: ProfileIn, user: dict = Depends(current_user)):
    conn = connect()
    try:
        before_row = conn.execute("SELECT github_username,github_snapshot FROM users WHERE id=?", (user["id"],)).fetchone()
        before_username = (before_row["github_username"] or "").strip() if before_row else ""
        before_snapshot = safe_json(before_row["github_snapshot"], {}) if before_row else {}
        mapping = {
            "name":"name", "college":"college", "course":"course", "year":"year", "bio":"bio", "city":"city", "skills":"skills", "interests":"interests", "roles":"roles", "availability":"availability", "work_preferences":"work_preferences", "profile_goal":"profile_goal", "github_username":"github_username", "github_url":"github_url", "linkedin_url":"linkedin_url", "portfolio_url":"portfolio_url", "projects":"projects", "hackathons":"hackathons", "discoverable":"discoverable", "preferred_mode":"preferred_mode", "travel_radius":"travel_radius", "notification_preference":"notification_preference"
        }
        vals = data.model_dump(exclude_unset=True)
        # If the user supplies a GitHub profile URL instead of a username, derive the public username
        # from a standard github.com/<username> URL so the evidence snapshot can be refreshed.
        gh_url = str(vals.get("github_url") or "").strip()
        if not str(vals.get("github_username") or "").strip() and gh_url:
            parsed = urllib.parse.urlparse(gh_url)
            if parsed.hostname and parsed.hostname.lower() in {"github.com", "www.github.com"}:
                path_parts = [part for part in parsed.path.split("/") if part]
                if path_parts and path_parts[0] not in {"settings", "features", "topics", "orgs", "login", "signup"}:
                    vals["github_username"] = path_parts[0]
        allowed_url_fields = ("github_url", "linkedin_url", "portfolio_url")
        for field in allowed_url_fields:
            val = vals.get(field)
            if val and not (val.startswith("https://") or val.startswith("http://")):
                raise HTTPException(status_code=422, detail=f"{field} must be a complete http(s) URL.")
        if vals.get("github_username") and not re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$", vals["github_username"]):
            raise HTTPException(status_code=422, detail="Enter a valid GitHub username.")
        updates = []
        params = []
        for field, val in vals.items():
            db_field = mapping[field]
            if field in ("skills","interests","roles","availability","work_preferences","projects","hackathons"):
                val = dumps(val)
            elif field == "discoverable":
                val = int(bool(val))
            updates.append(f"{db_field}=?")
            params.append(val)
        if updates:
            updates.append("updated_at=?")
            params.extend([now_iso(), user["id"]])
            conn.execute(f"UPDATE users SET {','.join(updates)} WHERE id=?", params)
            conn.commit()
            _log(conn, None, user["id"], "Profile Intelligence Agent", "Saved user-confirmed profile changes", {"fields": list(vals.keys())})
            conn.commit()
        # Public GitHub portfolio metadata is refreshed automatically when a username is
        # supplied/changed, or the cached snapshot is older than 30 days. Failure to reach
        # GitHub never prevents the user from saving the rest of their profile.
        latest_user = conn.execute("SELECT github_username,github_snapshot FROM users WHERE id=?", (user["id"],)).fetchone()
        github_username = (latest_user["github_username"] or "").strip() if latest_user else ""
        github_snapshot = safe_json(latest_user["github_snapshot"], {}) if latest_user else {}
        synced_at = github_snapshot.get("synced_at")
        stale = True
        if synced_at:
            try:
                synced_dt = datetime.fromisoformat(str(synced_at).replace("Z", "+00:00"))
                stale = (datetime.now(timezone.utc) - synced_dt).days >= 30
            except Exception:
                stale = True
        if not github_username:
            # Clearing a GitHub username also clears its cached evidence; otherwise the
            # score could continue reflecting an old account after the user disconnects it.
            if before_username or before_snapshot.get("synced_at"):
                conn.execute("UPDATE users SET github_snapshot='{}',updated_at=? WHERE id=?", (now_iso(), user["id"]))
                conn.commit()
                _log(conn, None, user["id"], "GitHub Research Agent", "Cleared cached GitHub evidence after profile disconnect", {})
                conn.commit()
        elif github_username != before_username or not synced_at or stale:
            snapshot = _fetch_github_snapshot(github_username)
            if snapshot:
                conn.execute("UPDATE users SET github_snapshot=?,updated_at=? WHERE id=?", (dumps(snapshot), now_iso(), user["id"]))
                conn.commit()
                _log(conn, None, user["id"], "GitHub Research Agent", "Refreshed public portfolio metadata during profile save", {"username": github_username, "public_repos": snapshot.get("public_repos", 0)})
                conn.commit()
            elif github_username != before_username and before_snapshot.get("synced_at"):
                # Never retain the old user's snapshot if a new username cannot be queried.
                conn.execute("UPDATE users SET github_snapshot='{}',updated_at=? WHERE id=?", (now_iso(), user["id"]))
                conn.commit()
        return {"user": _profile_dict(conn, user["id"], include_private=True)}
    finally:
        conn.close()

@app.get("/api/profile/{target_id}")
def view_profile(target_id: int, user: dict = Depends(current_user)):
    conn = connect()
    try:
        target = conn.execute("SELECT id,discoverable FROM users WHERE id=?", (target_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Profile not found.")
        member = conn.execute("SELECT 1 FROM memberships a JOIN memberships b ON a.team_id=b.team_id WHERE a.user_id=? AND b.user_id=? AND a.status='active' AND b.status='active'", (user["id"], target_id)).fetchone()
        if target_id != user["id"] and not target["discoverable"] and not member and not user["is_admin"]:
            raise HTTPException(status_code=403, detail="This student is not discoverable.")
        return {"profile": _profile_dict(conn, target_id, include_private=target_id == user["id"] or bool(user["is_admin"]))}
    finally:
        conn.close()

def _fetch_github_snapshot(username: str) -> dict | None:
    """Fetch a bounded public GitHub portfolio snapshot; no token required, optional token raises rate limits."""
    username = (username or "").strip()
    if not username:
        return None
    headers = {"User-Agent": "CampusConnect/1.0", "Accept": "application/vnd.github+json"}
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    try:
        base = f"https://api.github.com/users/{urllib.parse.quote(username)}"
        profile_req = urllib.request.Request(base, headers=headers)
        repos_req = urllib.request.Request(base + "/repos?sort=updated&per_page=100&type=owner", headers=headers)
        with urllib.request.urlopen(profile_req, timeout=5) as resp:
            github_profile = json.loads(resp.read().decode())
        with urllib.request.urlopen(repos_req, timeout=6) as resp:
            repos = json.loads(resp.read().decode())
        if not isinstance(github_profile, dict) or not isinstance(repos, list):
            return None
        now = datetime.now(timezone.utc)
        recent = 0
        described = 0
        summary_repos = []
        for repo in repos:
            if not isinstance(repo, dict) or repo.get("fork"):
                continue
            try:
                updated = datetime.fromisoformat(str(repo.get("updated_at", "")).replace("Z", "+00:00"))
                if (now - updated).days <= 365:
                    recent += 1
            except Exception:
                pass
            if repo.get("description"):
                described += 1
            summary_repos.append({"name": repo.get("full_name"), "description": repo.get("description") or "No description supplied", "language": repo.get("language"), "url": repo.get("html_url"), "updated_at": repo.get("updated_at"), "fork": bool(repo.get("fork")), "stars": int(repo.get("stargazers_count") or 0), "license": (repo.get("license") or {}).get("spdx_id") if repo.get("license") else None})
        summary_repos.sort(key=lambda r: (not r["description"], not r["language"], r["updated_at"] or ""), reverse=True)
        return {"synced_at": now_iso(), "username": username, "public_repos": int(github_profile.get("public_repos") or len(repos)), "recent_repos": recent, "described_repos": described, "repos": summary_repos[:12], "source": "GitHub public REST API", "profile_url": github_profile.get("html_url")}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError, KeyError, TypeError):
        return None


@app.post("/api/profile/github-sync")
def github_sync(user: dict = Depends(current_user)):
    """Compatibility endpoint. The profile UI refreshes GitHub metadata automatically on save."""
    conn = connect()
    try:
        row = conn.execute("SELECT github_username FROM users WHERE id=?", (user["id"],)).fetchone()
        username = (row["github_username"] or "").strip()
        if not username:
            raise HTTPException(status_code=400, detail="Add a GitHub username to your profile before syncing.")
        snapshot = _fetch_github_snapshot(username)
        if not snapshot:
            raise HTTPException(status_code=502, detail="GitHub could not be reached, rate-limited the request, or the username was not found. Your previous profile data was kept.")
        conn.execute("UPDATE users SET github_snapshot=?,updated_at=? WHERE id=?", (dumps(snapshot), now_iso(), user["id"]))
        conn.commit()
        _log(conn, None, user["id"], "GitHub Research Agent", "Synced public GitHub portfolio metadata", {"username": username, "public_repos": snapshot.get("public_repos", 0), "recent_repos": snapshot.get("recent_repos", 0)})
        conn.commit()
        return {"ok": True, "snapshot": snapshot, "profile": _profile_dict(conn, user["id"], include_private=True)}
    finally:
        conn.close()

# ------------------------- events -------------------------
@app.get("/api/events")
def list_events(q: str = "", location: str = "", category: str = "", status: str = "", user: dict = Depends(current_user)):
    conn = connect()
    try:
        sql = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []
        if q.strip():
            like = f"%{q.strip()}%"
            sql += " AND (name LIKE ? OR description LIKE ? OR category LIKE ? OR organizer LIKE ?)"
            params += [like, like, like, like]
        if location.strip() and location.lower() not in ("all", "anywhere"):
            if location.lower() == "online":
                sql += " AND (lower(mode)='online' OR lower(location)='online')"
            else:
                sql += " AND lower(location) LIKE ?"
                params.append(f"%{location.lower()}%")
        if category.strip() and category.lower() != "all":
            sql += " AND lower(category) LIKE ?"
            params.append(f"%{category.lower()}%")
        if status.strip() and status.lower() != "all":
            sql += " AND status=?"
            params.append(status.lower())
        sql += " ORDER BY CASE status WHEN 'registration_open' THEN 0 WHEN 'upcoming' THEN 1 WHEN 'ongoing' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END,event_start ASC"
        rows = conn.execute(sql, params).fetchall()
        return {"events": [_event_dict(conn, e, user["id"]) for e in rows], "count": len(rows), "data_mode": "Curated application database; demo records are explicitly labelled. Live event-source integration has not been configured."}
    finally:
        conn.close()

@app.get("/api/events/{event_id}")
def event_detail(event_id: int, user: dict = Depends(current_user)):
    conn = connect()
    try:
        return {"event": _event_dict(conn, _event(conn, event_id), user["id"])}
    finally:
        conn.close()

@app.post("/api/events/{event_id}/interest")
def event_interest(event_id: int, data: EventInterestIn, user: dict = Depends(current_user)):
    conn = connect()
    try:
        _event(conn, event_id)
        uid = user["id"]
        ts = now_iso()
        if data.interested:
            conn.execute("INSERT OR IGNORE INTO event_interactions(user_id,event_id,kind,created_at) VALUES(?,?,?,?)", (uid, event_id, "interested", ts))
        else:
            conn.execute("DELETE FROM event_interactions WHERE user_id=? AND event_id=? AND kind='interested'", (uid, event_id))
        if data.saved is True:
            conn.execute("INSERT OR IGNORE INTO event_interactions(user_id,event_id,kind,created_at) VALUES(?,?,?,?)", (uid, event_id, "saved", ts))
        elif data.saved is False:
            conn.execute("DELETE FROM event_interactions WHERE user_id=? AND event_id=? AND kind='saved'", (uid, event_id))
        conn.commit()
        _log(conn, None, uid, "Scout Agent", "Updated event interest", {"event_id": event_id, "interested": data.interested, "saved": data.saved})
        conn.commit()
        return {"event": _event_dict(conn, _event(conn, event_id), uid)}
    finally:
        conn.close()

@app.post("/api/admin/events")
def create_event(data: EventIn, user: dict = Depends(current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    if data.max_team_size < data.min_team_size:
        raise HTTPException(status_code=422, detail="Maximum team size must be at least the minimum team size.")
    for field in ("registration_url", "source_url"):
        value = getattr(data, field)
        if value and not value.startswith(("https://", "http://")):
            raise HTTPException(status_code=422, detail=f"{field} must be a complete http(s) URL.")
    conn = connect()
    try:
        ts = now_iso()
        cur = conn.execute("INSERT INTO events(name,description,organizer,category,mode,location,event_start,event_end,registration_deadline,min_team_size,max_team_size,required_skills,eligibility,registration_url,source_url,source_label,last_verified_at,status,is_demo,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)", (data.name, data.description, data.organizer, data.category, data.mode, data.location, data.event_start, data.event_end, data.registration_deadline, data.min_team_size, data.max_team_size, dumps(data.required_skills), data.eligibility, data.registration_url, data.source_url, data.source_label, ts, data.status, user["id"], ts))
        conn.commit()
        _log(conn, None, user["id"], "Scout Agent", "Added an administrator-supplied event", {"event_id": cur.lastrowid, "source_url": data.source_url, "source_label": data.source_label})
        conn.commit()
        return {"event": _event_dict(conn, _event(conn, cur.lastrowid), user["id"])}
    finally:
        conn.close()

@app.post("/api/event-proposals")
def propose_event(data: dict, user: dict = Depends(current_user)):
    name = str(data.get("name", "")).strip()[:160]
    url = str(data.get("url", "")).strip()[:500]
    reason = str(data.get("reason", "")).strip()[:1000]
    if len(name) < 3:
        raise HTTPException(status_code=422, detail="Add an event name.")
    if url and not url.startswith(("https://", "http://")):
        raise HTTPException(status_code=422, detail="URL must begin with http:// or https://.")
    conn = connect()
    try:
        cur = conn.execute("INSERT INTO event_proposals(proposer_id,name,url,reason,status,created_at) VALUES(?,?,?,?,?,?)", (user["id"], name, url, reason, "pending", now_iso()))
        conn.commit()
        for a in conn.execute("SELECT id FROM users WHERE is_admin=1").fetchall():
            _message(conn, a["id"], "New event suggestion", f"{user['name']} suggested an event: {name}", msg_type="event_proposal", sender_id=user["id"], data={"proposal_id": cur.lastrowid})
        conn.commit()
        return {"ok": True, "proposal_id": cur.lastrowid, "message": "Event suggestion submitted for admin review."}
    finally:
        conn.close()

# ------------------------- teams / matching -------------------------
@app.get("/api/teams")
def my_teams(user: dict = Depends(current_user)):
    conn = connect()
    try:
        rows = conn.execute("SELECT DISTINCT t.* FROM teams t LEFT JOIN memberships m ON m.team_id=t.id AND m.status='active' WHERE m.user_id=? OR t.captain_id=? ORDER BY t.updated_at DESC", (user["id"], user["id"])).fetchall()
        return {"teams": [_team_card(conn, r, user["id"]) for r in rows]}
    finally:
        conn.close()

@app.get("/api/teams/open")
def open_teams(event_id: int | None = None, q: str = "", user: dict = Depends(current_user)):
    conn = connect()
    try:
        sql = "SELECT t.* FROM teams t JOIN events e ON e.id=t.event_id WHERE t.status IN ('recruiting','active','recovery_needed') AND e.status IN ('upcoming','registration_open','ongoing')"
        params: list[Any] = []
        if event_id:
            sql += " AND t.event_id=?"
            params.append(event_id)
        if q.strip():
            like = f"%{q.strip()}%"
            sql += " AND (t.name LIKE ? OR t.goal LIKE ? OR e.name LIKE ?)"
            params += [like, like, like]
        sql += " ORDER BY e.event_start,t.created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            card = _team_card(conn, row, user["id"])
            if card["primary_slots_available_to_request"] > 0 or card["backup_slot_available"]:
                result.append(card)
        return {"teams": result}
    finally:
        conn.close()

@app.post("/api/teams")
def create_team(data: CreateTeamIn, user: dict = Depends(current_user)):
    conn = connect()
    try:
        event = _check_open_event(conn, data.event_id)
        if _active_commitment(conn, user["id"], data.event_id):
            raise HTTPException(status_code=409, detail="You already have an active primary or backup commitment for this event.")
        ts = now_iso()
        cur = conn.execute("INSERT INTO teams(event_id,name,captain_id,primary_limit,goal,status,created_at,updated_at) VALUES(?,?,?,?,?,'recruiting',?,?)", (data.event_id, data.name.strip(), user["id"], int(event["max_team_size"]), data.goal.strip(), ts, ts))
        team_id = cur.lastrowid
        _add_membership(conn, team_id, user["id"], "primary", "Team captain")
        run_id = log_simple_run(conn, team_id, user["id"], "Create a new team for an event", [("Scout Agent", "Loaded event constraints", {"event_id": event["id"], "max_team_size": event["max_team_size"], "required_skills": safe_json(event["required_skills"])}), ("Team Architect Agent", "Created initial team with captain as a primary member", {"team_id": team_id, "primary_slots_open": int(event["max_team_size"]) - 1})], result={"team_id": team_id})
        conn.execute("UPDATE teams SET updated_at=? WHERE id=?", (now_iso(), team_id))
        conn.commit()
        return {"team": _team_card(conn, conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone(), user["id"]), "run_id": run_id}
    finally:
        conn.close()

@app.get("/api/teams/{team_id}")
def team_detail(team_id: int, user: dict = Depends(current_user)):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Team not found.")
        team = dict(row)
        active_member = conn.execute("SELECT 1 FROM memberships WHERE team_id=? AND user_id=? AND status='active'", (team_id, user["id"])).fetchone()
        event = _event(conn, team["event_id"])
        if not active_member and team["captain_id"] != user["id"] and (team["status"] == "completed" or event["status"] in ("completed", "cancelled")) and not user["is_admin"]:
            raise HTTPException(status_code=403, detail="This team workspace is private.")
        card = _team_card(conn, row, user["id"])
        member_details = []
        for m in _active_members(conn, team_id):
            p = _profile_dict(conn, m["user_id"])
            member_details.append({"membership_id":m["id"],"user_id":m["user_id"],"role":m["role"],"role_label":m["role_label"],"status":m["status"],"joined_at":m["joined_at"],"profile":p})
        is_captain = team["captain_id"] == user["id"]
        pending = []
        if is_captain:
            reqs = conn.execute("SELECT r.*,u.name AS other_name,u.college AS other_college,u.city AS other_city FROM requests r JOIN users u ON u.id=CASE WHEN r.from_user_id=? THEN r.to_user_id ELSE r.from_user_id END WHERE r.team_id=? AND r.status='pending' ORDER BY r.created_at DESC", (user["id"], team_id)).fetchall()
            pending = [{"id":r["id"],"kind":r["kind"],"role":r["offered_role"],"role_label":r["role_label"],"message":r["message"],"created_at":r["created_at"],"other_user_id":r["to_user_id"] if r["from_user_id"]==user["id"] else r["from_user_id"],"other_name":r["other_name"],"other_college":r["other_college"],"other_city":r["other_city"],"incoming":r["to_user_id"]==user["id"],"request_id":r["id"]} for r in reqs]
        latest_runs = []
        if is_captain:
            runs = conn.execute("SELECT * FROM agent_runs WHERE team_id=? ORDER BY id DESC LIMIT 5", (team_id,)).fetchall()
            latest_runs = [{"id":r["id"],"goal":r["goal"],"status":r["status"],"created_at":r["created_at"],"result":safe_json(r["result"], {})} for r in runs]
        reviewables = []
        if event["status"] == "completed" or team["status"] == "completed":
            reviewed = {r["target_id"] for r in conn.execute("SELECT target_id FROM reviews WHERE team_id=? AND reviewer_id=?", (team_id,user["id"])).fetchall()}
            for m in _active_members(conn, team_id, "primary"):
                if m["user_id"] != user["id"]:
                    reviewables.append({"user_id":m["user_id"],"name":m["name"],"already_reviewed":m["user_id"] in reviewed})
        return {"team": card, "event": _event_dict(conn, conn.execute("SELECT * FROM events WHERE id=?", (team["event_id"],)).fetchone(), user["id"]), "members": member_details, "pending_requests": pending, "latest_runs": latest_runs, "reviewables": reviewables, "can_manage": is_captain, "can_review": bool(reviewables) and (event["status"] == "completed" or team["status"] == "completed"), "viewer_membership": dict(active_member) if active_member else None}
    finally:
        conn.close()

@app.post("/api/teams/{team_id}/match")
def team_match(team_id: int, data: MatchIn, user: dict = Depends(current_user)):
    conn = connect()
    try:
        team = _require_captain(conn, team_id, user["id"])
        event = _check_open_event(conn, team["event_id"])
        counts = _counts(conn, team)
        if data.target_role == "primary" and counts["primary_slots_available_to_request"] <= 0:
            raise HTTPException(status_code=409, detail="No primary invitation slots are available. Resolve pending requests first.")
        if data.target_role == "backup" and not counts["backup_slot_available"]:
            raise HTTPException(status_code=409, detail="The team's one backup slot is already filled or reserved.")
        conn.execute("INSERT OR IGNORE INTO event_interactions(user_id,event_id,kind,created_at) VALUES(?,?,?,?)", (user["id"], event["id"], "team_search", now_iso()))
        conn.commit()
        result = run_team_architect(conn, team_id, user["id"], max_results=8, recovery=data.recovery, target_role=data.target_role, run_goal=data.goal)
        invite_limit = min(len(result.get("candidates", [])), 1 if data.target_role == "backup" else counts["primary_slots_available_to_request"])
        result["target_role"] = data.target_role
        result["invite_limit"] = max(0, int(invite_limit))
        result["suggested_candidate_ids"] = [int(c["user"]["id"]) for c in result.get("candidates", [])[:invite_limit] if c.get("user", {}).get("id") is not None]
        result["candidate_note"] = "Compatibility scores are private to this captain matching workflow. They are not shown on candidates' own profiles or join-application screens."
        if data.target_role == "backup":
            result["summary"] = result["summary"] + " This search is for the single reserve slot; event substitute rules must be checked before treating the reserve as an official entrant."
        conn.execute("UPDATE agent_runs SET result=? WHERE id=? AND team_id=? AND actor_id=?", (dumps({k:v for k,v in result.items() if k != "logs"}), int(result["run_id"]), team_id, user["id"]))
        conn.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    finally:
        conn.close()

@app.get("/api/agent/runs/{run_id}")
def get_agent_run(run_id: int, user: dict = Depends(current_user)):
    conn = connect()
    try:
        row = conn.execute("SELECT ar.* FROM agent_runs ar LEFT JOIN teams t ON t.id=ar.team_id WHERE ar.id=?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Agent run not found.")
        if row["team_id"]:
            _require_captain(conn, row["team_id"], user["id"])
        return {"run": {"id":row["id"],"team_id":row["team_id"],"goal":row["goal"],"status":row["status"],"result":safe_json(row["result"], {}),"created_at":row["created_at"],"completed_at":row["completed_at"]}, "events": [_agent_event_dict(r) for r in conn.execute("SELECT * FROM agent_events WHERE run_id=? ORDER BY id", (run_id,)).fetchall()]}
    finally:
        conn.close()

@app.get("/api/agent/activity")
def agent_activity(team_id: int | None = None, limit: int = 50, user: dict = Depends(current_user)):
    conn = connect()
    try:
        limit = min(100, max(1, limit))
        if team_id:
            team = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
            if not team:
                raise HTTPException(status_code=404, detail="Team not found.")
            if team["captain_id"] != user["id"] and not conn.execute("SELECT 1 FROM memberships WHERE team_id=? AND user_id=? AND status='active'", (team_id,user["id"])).fetchone() and not user["is_admin"]:
                raise HTTPException(status_code=403, detail="You cannot view this team's activity.")
            rows = conn.execute("SELECT * FROM agent_events WHERE team_id=? ORDER BY id DESC LIMIT ?", (team_id,limit)).fetchall()
        else:
            rows = conn.execute("SELECT ae.* FROM agent_events ae LEFT JOIN memberships m ON m.team_id=ae.team_id AND m.user_id=? AND m.status='active' LEFT JOIN teams t ON t.id=ae.team_id WHERE ae.actor_id=? OR m.user_id=? OR t.captain_id=? GROUP BY ae.id ORDER BY ae.id DESC LIMIT ?", (user["id"],user["id"],user["id"],user["id"],limit)).fetchall()
        return {"events": [_agent_event_dict(r) for r in rows]}
    finally:
        conn.close()

@app.post("/api/teams/{team_id}/requests")
def send_team_request(team_id: int, data: RequestIn, user: dict = Depends(current_user)):
    conn = connect()
    try:
        team = _require_captain(conn, team_id, user["id"])
        if team["status"] == "completed":
            raise HTTPException(status_code=400, detail="Completed teams cannot invite members.")
        _check_open_event(conn, team["event_id"])
        candidate = conn.execute("SELECT id,name,discoverable FROM users WHERE id=?", (data.candidate_id,)).fetchone()
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found.")
        if not candidate["discoverable"]:
            raise HTTPException(status_code=403, detail="This student is not discoverable.")
        request_id = _create_request(conn, team=team, from_user_id=user["id"], to_user_id=data.candidate_id, kind="invite", role=data.role, role_label=data.role_label.strip(), message=data.message.strip())
        _message(conn, data.candidate_id, f"Invitation to join {team['name']}", f"{user['name']} invited you to join as a {data.role}. Review the event and team before deciding.", msg_type="team_invitation", sender_id=user["id"], data={"team_id": team_id, "request_id": request_id, "role": data.role})
        _log(conn, team_id, user["id"], "Consent & Coordination Agent", "Created a captain-approved invitation", {"request_id": request_id, "candidate_id": data.candidate_id, "role": data.role})
        conn.commit()
        return {"ok": True, "request_id": request_id, "message": "Invitation created and added to the candidate's inbox."}
    finally:
        conn.close()

@app.post("/api/teams/{team_id}/requests/bulk")
def send_bulk_team_requests(team_id: int, data: BulkRequestIn, user: dict = Depends(current_user)):
    """Invite the Team Architect's suggested set in one captain action.

    Every invitation remains a separate pending request for its recipient to accept or decline.
    The endpoint only invites candidates in the specified authorised run and caps invitations by
    the current available primary slots or the team's single backup slot.
    """
    conn = connect()
    invited: list[dict] = []
    skipped: list[dict] = []
    try:
        team = _require_captain(conn, team_id, user["id"])
        if team["status"] == "completed":
            raise HTTPException(status_code=400, detail="Completed teams cannot invite members.")
        _check_open_event(conn, team["event_id"])
        run = conn.execute("SELECT * FROM agent_runs WHERE id=? AND team_id=? AND actor_id=?", (data.run_id, team_id, user["id"])).fetchone()
        if not run:
            raise HTTPException(status_code=404, detail="Team Architect run not found for this captain.")
        run_result = safe_json(run["result"], {})
        if run_result.get("target_role") != data.role:
            raise HTTPException(status_code=409, detail="The selected run does not match this invitation role. Run Team Architect again.")
        allowed = {int(x) for x in run_result.get("suggested_candidate_ids", [])}
        ids = list(dict.fromkeys(int(x) for x in data.candidate_ids))
        if not ids or any(x not in allowed for x in ids):
            raise HTTPException(status_code=400, detail="Bulk invitations can only use candidates in the suggested team for this run.")
        counts = _counts(conn, team)
        available = counts["primary_slots_available_to_request"] if data.role == "primary" else (1 if counts["backup_slot_available"] else 0)
        if available <= 0:
            raise HTTPException(status_code=409, detail="No invitation slots remain for this role. Refresh Team Architect to recalculate the suggestions.")
        if data.role == "backup" and len(ids) > 1:
            ids = ids[:1]
        elif data.role == "primary":
            ids = ids[:available]
        for candidate_id in ids:
            try:
                candidate = conn.execute("SELECT id,name,discoverable FROM users WHERE id=?", (candidate_id,)).fetchone()
                if not candidate:
                    raise HTTPException(status_code=404, detail="Candidate profile no longer exists.")
                if not candidate["discoverable"]:
                    raise HTTPException(status_code=403, detail="This student is no longer discoverable.")
                request_id = _create_request(conn, team=team, from_user_id=user["id"], to_user_id=candidate_id, kind="invite", role=data.role, role_label="Designated backup" if data.role == "backup" else "Primary member", message=data.message.strip())
                _message(conn, candidate_id, f"Invitation to join {team['name']}", f"{user['name']} invited you to join as a {data.role}. Review the event and team before deciding.", msg_type="team_invitation", sender_id=user["id"], data={"team_id": team_id, "request_id": request_id, "role": data.role})
                _log(conn, team_id, user["id"], "Consent & Coordination Agent", "Created invitation from suggested team batch", {"request_id": request_id, "candidate_id": candidate_id, "role": data.role, "agent_run_id": data.run_id})
                conn.commit()
                invited.append({"candidate_id": candidate_id, "name": candidate["name"], "request_id": request_id})
            except HTTPException as exc:
                conn.rollback()
                skipped.append({"candidate_id": candidate_id, "reason": exc.detail})
        # Once invitations are issued, consume these suggested slots so a stale run cannot invite them twice.
        if invited:
            fresh_counts = _counts(conn, team)
            remaining = fresh_counts["primary_slots_available_to_request"] if data.role == "primary" else (1 if fresh_counts["backup_slot_available"] else 0)
            run_result["suggested_candidate_ids"] = []
            run_result["invite_limit"] = 0
            run_result["invited_candidate_ids"] = [x["candidate_id"] for x in invited]
            conn.execute("UPDATE agent_runs SET result=? WHERE id=?", (dumps(run_result), data.run_id))
            conn.commit()
        return {"ok": bool(invited), "invited": invited, "skipped": skipped, "invited_count": len(invited), "skipped_count": len(skipped), "message": f"Sent {len(invited)} invitation(s). Each person must sign in and accept or decline individually."}
    finally:
        conn.close()


@app.post("/api/teams/{team_id}/apply")
def apply_to_team(team_id: int, data: ApplyIn, user: dict = Depends(current_user)):
    conn = connect()
    try:
        team_row = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
        if not team_row:
            raise HTTPException(status_code=404, detail="Team not found.")
        team = dict(team_row)
        if team["captain_id"] == user["id"]:
            raise HTTPException(status_code=400, detail="You are already the captain of this team.")
        _check_open_event(conn, team["event_id"])
        if not user["discoverable"]:
            # Applying is an explicit opt-in; doesn't require global discoverability.
            pass
        if _active_commitment(conn, user["id"], team["event_id"]):
            raise HTTPException(status_code=409, detail="You already have a commitment for this event.")
        # Applications are also subject to event-size/backup-slot constraints.
        counts = _counts(conn, team)
        if data.role == "primary" and counts["primary_count"] + counts["pending_primary_requests"] >= int(team["primary_limit"]):
            raise HTTPException(status_code=409, detail="This team has no available primary slots.")
        if data.role == "backup" and (not counts["backup_slot_available"]):
            raise HTTPException(status_code=409, detail="This team already has a designated or pending backup.")
        request_id = _create_request(conn, team=team, from_user_id=user["id"], to_user_id=team["captain_id"], kind="application", role=data.role, role_label=data.role_label.strip(), message=data.message.strip())
        _message(conn, team["captain_id"], f"{user['name']} applied to {team['name']}", f"{user['name']} requested to join as a {data.role}. Open the team workspace to accept or decline.", msg_type="team_application", sender_id=user["id"], data={"team_id": team_id, "request_id": request_id, "role": data.role})
        conn.commit()
        return {"ok": True, "request_id": request_id, "message": "Your request was sent to the team captain."}
    finally:
        conn.close()

@app.post("/api/requests/{request_id}/respond")
def respond_request(request_id: int, data: RespondIn, user: dict = Depends(current_user)):
    conn = connect()
    try:
        req = _get_request(conn, request_id)
        if req["status"] != "pending":
            raise HTTPException(status_code=409, detail="This request is no longer pending.")
        if req["to_user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Only the recipient can respond to this request.")
        team_row = conn.execute("SELECT * FROM teams WHERE id=?", (req["team_id"],)).fetchone()
        if not team_row:
            raise HTTPException(status_code=404, detail="Team no longer exists.")
        team = dict(team_row)
        event = _event(conn, team["event_id"])
        if data.action == "decline":
            conn.execute("UPDATE requests SET status='declined',responded_at=? WHERE id=?", (now_iso(), request_id))
            _notify_request_result(conn, req, "declined")
            _log(conn, team["id"], user["id"], "Consent & Coordination Agent", "Recorded a declined request", {"request_id": request_id, "kind": req["kind"], "role": req["offered_role"]})
            conn.commit()
            recovery_run = None
            if req["kind"] in ("invite", "promotion") and team["status"] != "completed" and event["status"] not in ("completed", "closed", "cancelled"):
                captain_id = team["captain_id"]
                # Agentic recovery searches and recommends alternatives. It does not send another invitation without captain approval.
                try:
                    fresh_team = conn.execute("SELECT * FROM teams WHERE id=?", (team["id"],)).fetchone()
                    counts = _counts(conn, dict(fresh_team))
                    target_role = "backup" if req["offered_role"] == "backup" else "primary"
                    if (target_role == "primary" and counts["primary_slots_available_to_request"] > 0) or (target_role == "backup" and counts["backup_slot_available"]):
                        res = run_team_architect(conn, team["id"], captain_id, max_results=8, recovery=True, target_role=target_role, run_goal=f"Recover from a declined {req['offered_role']} request")
                        recovery_run = res["run_id"]
                        _message(conn, captain_id, "Team recovery shortlist is ready", f"A candidate declined. The Recovery Agent has re-evaluated the team and prepared {len(res['candidates'])} alternative candidate(s). Review them before sending another invitation.", msg_type="recovery_shortlist", data={"team_id": team["id"], "run_id": recovery_run, "target_role": target_role})
                        conn.commit()
                except Exception:
                    # The declined request remains safely recorded even if recovery search fails.
                    pass
            return {"ok": True, "status": "declined", "team_id": team["id"], "recovery_run_id": recovery_run, "message": "Your response was recorded."}

        # Acceptance: validate current state again before writing the membership.
        if event["status"] in ("completed", "closed", "cancelled") or (event["registration_deadline"] and event["registration_deadline"] < datetime.now(timezone.utc).date().isoformat()):
            conn.execute("UPDATE requests SET status='expired',responded_at=? WHERE id=?", (now_iso(), request_id))
            conn.commit()
            raise HTTPException(status_code=409, detail="The event is no longer accepting membership changes.")
        if req["kind"] == "promotion":
            # to_user is the backup, from_user is captain.
            vacancy = conn.execute("SELECT COUNT(*) FROM memberships WHERE team_id=? AND role='primary' AND status='active'", (team["id"],)).fetchone()[0] < int(team["primary_limit"])
            backup = conn.execute("SELECT * FROM memberships WHERE team_id=? AND user_id=? AND role='backup' AND status='active'", (team["id"], user["id"])).fetchone()
            if not vacancy or not backup:
                raise HTTPException(status_code=409, detail="The primary vacancy or backup membership has changed. Ask the captain to refresh the recovery options.")
            conn.execute("UPDATE memberships SET role='primary',role_label='Promoted from backup',joined_at=? WHERE id=?", (now_iso(), backup["id"]))
            conn.execute("UPDATE teams SET status='recruiting',updated_at=? WHERE id=?", (now_iso(), team["id"]))
        else:
            new_member_id = req["to_user_id"] if req["kind"] == "invite" else req["from_user_id"]
            _request_slots_check(conn, team, req["offered_role"], request_id=request_id)
            if _active_commitment(conn, new_member_id, team["event_id"], exclude_team=team["id"]):
                raise HTTPException(status_code=409, detail="This student committed to another team for this event before accepting.")
            _add_membership(conn, team["id"], new_member_id, req["offered_role"], req["role_label"] or ("Primary member" if req["offered_role"] == "primary" else "Backup member"))
            # If the applicant accepted their own request, or invitee accepted an invitation, cancel other pending requests for the same event.
            others = conn.execute("SELECT r.* FROM requests r JOIN teams t ON t.id=r.team_id WHERE t.event_id=? AND r.id<>? AND r.status='pending' AND ((r.kind='invite' AND r.to_user_id=?) OR (r.kind='application' AND r.from_user_id=?))", (team["event_id"], request_id, new_member_id, new_member_id)).fetchall()
            for other in others:
                conn.execute("UPDATE requests SET status='cancelled',responded_at=? WHERE id=?", (now_iso(), other["id"]))
                _message(conn, other["from_user_id"] if other["kind"] == "invite" else other["to_user_id"], "Pending team request cancelled", "This student has accepted another team commitment for the same event, so the pending request was closed.", msg_type="request_update", data={"team_id": other["team_id"], "request_id": other["id"]})
        conn.execute("UPDATE requests SET status='accepted',responded_at=? WHERE id=?", (now_iso(), request_id))
        _notify_request_result(conn, req, "accepted")
        _log(conn, team["id"], user["id"], "Consent & Coordination Agent", "Recorded accepted request and updated team state", {"request_id": request_id, "kind": req["kind"], "role": req["offered_role"]})
        # Update status based on active primary count.
        active_primary = conn.execute("SELECT COUNT(*) FROM memberships WHERE team_id=? AND status='active' AND role='primary'", (team["id"],)).fetchone()[0]
        if active_primary >= int(team["primary_limit"]):
            conn.execute("UPDATE teams SET status='active',updated_at=? WHERE id=?", (now_iso(), team["id"]))
        else:
            conn.execute("UPDATE teams SET status='recruiting',updated_at=? WHERE id=?", (now_iso(), team["id"]))
        conn.commit()
        return {"ok": True, "status": "accepted", "team_id": team["id"], "message": "Your response was recorded and the team state was updated."}
    finally:
        conn.close()

@app.post("/api/teams/{team_id}/withdraw")
def withdraw_from_team(team_id: int, data: WithdrawIn, user: dict = Depends(current_user)):
    conn = connect()
    try:
        team_row = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
        if not team_row:
            raise HTTPException(status_code=404, detail="Team not found.")
        team = dict(team_row)
        if team["captain_id"] == user["id"]:
            raise HTTPException(status_code=400, detail="The captain must transfer captaincy before leaving this team.")
        member = conn.execute("SELECT * FROM memberships WHERE team_id=? AND user_id=? AND status='active'", (team_id, user["id"])).fetchone()
        if not member:
            raise HTTPException(status_code=404, detail="You are not an active member of this team.")
        role = member["role"]
        conn.execute("UPDATE memberships SET status='withdrawn',withdrawn_at=? WHERE id=?", (now_iso(), member["id"]))
        conn.execute("UPDATE teams SET status=?,updated_at=? WHERE id=?", ("recovery_needed" if role == "primary" else "recruiting", now_iso(), team_id))
        _log(conn, team_id, user["id"], "Backup & Recovery Agent", "Recorded team-member withdrawal", {"user_id": user["id"], "role": role, "reason": data.reason.strip()[:300]})
        captain_id = team["captain_id"]
        _message(conn, captain_id, "A team member withdrew", f"{user['name']} withdrew from {team['name']} as a {role}. The roster and skill gaps need to be reviewed.", msg_type="member_withdrawal", sender_id=user["id"], data={"team_id": team_id, "user_id": user["id"], "role": role})
        conn.commit()
        run_id = None
        if role == "primary" and team["status"] != "completed":
            try:
                fresh = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
                fresh_event = _event(conn, team["event_id"])
                if fresh_event["status"] not in ("completed", "closed", "cancelled"):
                    result = run_team_architect(conn, team_id, captain_id, max_results=8, recovery=True, target_role="primary", run_goal=f"Recover after {user['name']} withdrew")
                    run_id = result["run_id"]
                    backup = conn.execute("SELECT u.id,u.name,m.role_label FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.team_id=? AND m.role='backup' AND m.status='active'", (team_id,)).fetchone()
                    _message(conn, captain_id, "Team recovery required", (f"A primary member withdrew. Your designated backup is {backup['name']}. Choose whether to send a promotion request or recruit a replacement." if backup else "A primary member withdrew and no backup is designated. The Recovery Agent has prepared a candidate shortlist."), msg_type="recovery_shortlist", data={"team_id": team_id, "run_id": run_id, "backup_user_id": backup["id"] if backup else None, "primary_vacancy": True})
                    conn.commit()
            except Exception:
                pass
        return {"ok": True, "role": role, "recovery_run_id": run_id, "message": "Your membership status was updated."}
    finally:
        conn.close()

@app.post("/api/teams/{team_id}/promote-backup")
def promote_backup(team_id: int, user: dict = Depends(current_user)):
    conn = connect()
    try:
        team = _require_captain(conn, team_id, user["id"])
        _check_open_event(conn, team["event_id"])
        primary_count = conn.execute("SELECT COUNT(*) FROM memberships WHERE team_id=? AND role='primary' AND status='active'", (team_id,)).fetchone()[0]
        if primary_count >= int(team["primary_limit"]):
            raise HTTPException(status_code=409, detail="There is no open primary slot to fill.")
        backup = conn.execute("SELECT m.*,u.name FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.team_id=? AND m.role='backup' AND m.status='active'", (team_id,)).fetchone()
        if not backup:
            raise HTTPException(status_code=404, detail="This team does not have a designated backup.")
        existing = conn.execute("SELECT id FROM requests WHERE team_id=? AND to_user_id=? AND kind='promotion' AND status='pending'", (team_id, backup["user_id"])).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="A promotion request is already pending.")
        ts = now_iso()
        cur = conn.execute("INSERT INTO requests(team_id,from_user_id,to_user_id,kind,offered_role,role_label,message,status,created_at) VALUES(?,?,?,'promotion','promote_backup',?,?, 'pending',?)", (team_id, user["id"], backup["user_id"], backup["role_label"], "The captain asks you to become an active primary member. Accept only if you can commit.", ts))
        req_id = cur.lastrowid
        _message(conn, backup["user_id"], "Backup promotion request", f"The captain of {team['name']} wants to promote you from backup to primary. Accept only if you can commit to the event.", msg_type="backup_promotion", sender_id=user["id"], data={"team_id": team_id, "request_id": req_id})
        _log(conn, team_id, user["id"], "Backup & Recovery Agent", "Sent captain-approved backup promotion request", {"request_id": req_id, "backup_user_id": backup["user_id"]})
        conn.commit()
        return {"ok": True, "request_id": req_id, "message": "Promotion request sent. The backup must accept before the roster changes."}
    finally:
        conn.close()

# ------------------------- inbox -------------------------
@app.get("/api/inbox")
def inbox(unread_only: bool = False, user: dict = Depends(current_user)):
    conn = connect()
    try:
        sql = "SELECT * FROM messages WHERE recipient_id=?"
        params: list[Any] = [user["id"]]
        if unread_only:
            sql += " AND is_read=0"
        sql += " ORDER BY created_at DESC LIMIT 150"
        rows = conn.execute(sql, params).fetchall()
        unread = conn.execute("SELECT COUNT(*) FROM messages WHERE recipient_id=? AND is_read=0", (user["id"],)).fetchone()[0]
        return {"messages": [_message_dict(conn, row) for row in rows], "unread_count": int(unread)}
    finally:
        conn.close()

@app.post("/api/inbox/{message_id}/read")
def read_message(message_id: int, user: dict = Depends(current_user)):
    conn = connect()
    try:
        cur = conn.execute("UPDATE messages SET is_read=1 WHERE id=? AND recipient_id=?", (message_id, user["id"]))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Message not found.")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@app.post("/api/inbox/read-all")
def read_all(user: dict = Depends(current_user)):
    conn = connect()
    try:
        conn.execute("UPDATE messages SET is_read=1 WHERE recipient_id=? AND is_read=0", (user["id"],))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

# ------------------------- reviews / deserter reports -------------------------
@app.get("/api/teams/{team_id}/reviewables")
def reviewables(team_id: int, user: dict = Depends(current_user)):
    conn = connect()
    try:
        team_row = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
        if not team_row:
            raise HTTPException(status_code=404, detail="Team not found.")
        team = dict(team_row)
        event = _event(conn, team["event_id"])
        member = conn.execute("SELECT 1 FROM memberships WHERE team_id=? AND user_id=? AND status='active'", (team_id, user["id"])).fetchone()
        if not member:
            raise HTTPException(status_code=403, detail="Only confirmed team members can review teammates.")
        if event["status"] != "completed" and team["status"] != "completed":
            raise HTTPException(status_code=400, detail="Reviews are available after the event is completed.")
        members = _active_members(conn, team_id, "primary")
        items = []
        for m in members:
            if m["user_id"] == user["id"]:
                continue
            existing_review = conn.execute("SELECT id FROM reviews WHERE event_id=? AND reviewer_id=? AND target_id=?", (event["id"], user["id"], m["user_id"])).fetchone()
            existing_report = conn.execute("SELECT id FROM deserter_reports WHERE event_id=? AND reporter_id=? AND target_id=?", (event["id"], user["id"], m["user_id"])).fetchone()
            items.append({"user_id":m["user_id"],"name":m["name"],"role_label":m["role_label"],"already_reviewed":bool(existing_review),"already_reported":bool(existing_report)})
        return {"event": _event_dict(conn, conn.execute("SELECT * FROM events WHERE id=?", (event["id"],)).fetchone(), user["id"]), "team_id": team_id, "members": items}
    finally:
        conn.close()

@app.post("/api/teams/{team_id}/reviews")
def submit_review(team_id: int, data: ReviewIn, user: dict = Depends(current_user)):
    conn = connect()
    try:
        team_row = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
        if not team_row:
            raise HTTPException(status_code=404, detail="Team not found.")
        team = dict(team_row)
        event = _event(conn, team["event_id"])
        if event["status"] != "completed" and team["status"] != "completed":
            raise HTTPException(status_code=400, detail="Reviews can only be submitted after the event completes.")
        reviewer = conn.execute("SELECT role,status FROM memberships WHERE team_id=? AND user_id=?", (team_id, user["id"])).fetchone()
        target = conn.execute("SELECT role,status FROM memberships WHERE team_id=? AND user_id=?", (team_id, data.target_id)).fetchone()
        if not reviewer or reviewer["role"] != "primary" or reviewer["status"] != "active":
            raise HTTPException(status_code=403, detail="Only confirmed primary teammates can submit peer reviews.")
        if not target or target["role"] != "primary" or data.target_id == user["id"]:
            raise HTTPException(status_code=400, detail="Choose another confirmed primary teammate to review.")
        if data.report_deserter and len(data.desertion_details.strip()) < 10:
            raise HTTPException(status_code=422, detail="Add a short factual explanation (at least 10 characters) for a desertion report.")
        if data.report_deserter and conn.execute("SELECT 1 FROM deserter_reports WHERE event_id=? AND reporter_id=? AND target_id=?", (event["id"], user["id"], data.target_id)).fetchone():
            raise HTTPException(status_code=409, detail="You have already submitted a desertion report for this teammate and event.")
        ts = now_iso()
        try:
            conn.execute("INSERT INTO reviews(team_id,event_id,reviewer_id,target_id,contribution,reliability,teamwork,communication,role_fulfilment,comment,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,'active',?)", (team_id, event["id"], user["id"], data.target_id, data.contribution, data.reliability, data.teamwork, data.communication, data.role_fulfilment, data.comment.strip(), ts))
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=409, detail="You have already reviewed this teammate for this event.")
            raise
        _log(conn, team_id, user["id"], "Trust & Review Agent", "Recorded eligible teammate review", {"target_id": data.target_id, "event_id": event["id"], "ratings": {"contribution": data.contribution, "reliability": data.reliability, "teamwork": data.teamwork, "communication": data.communication, "role_fulfilment": data.role_fulfilment}})
        conn.commit()
        report_result = None
        if data.report_deserter:
            report_result = _record_deserter_report(conn, team_id, user["id"], data.target_id, data.desertion_details)
        return {"ok": True, "message": "Teammate review saved.", "deserter_report": report_result, "profile_score": calculate_profile_score(conn, data.target_id)}
    finally:
        conn.close()

@app.post("/api/teams/{team_id}/deserter-reports")
def report_deserter(team_id: int, data: DeserterReportIn, user: dict = Depends(current_user)):
    conn = connect()
    try:
        result = _record_deserter_report(conn, team_id, user["id"], data.target_id, data.explanation)
        return {"ok": True, "message": "Your report was recorded. It will only create a Possible Deserter warning if the eligibility threshold is reached.", "case": result}
    finally:
        conn.close()

@app.post("/api/deserter-cases/{case_id}/respond")
def respond_deserter_case(case_id: int, data: AppealIn, user: dict = Depends(current_user)):
    conn = connect()
    try:
        case = conn.execute("SELECT * FROM deserter_cases WHERE id=?", (case_id,)).fetchone()
        if not case:
            raise HTTPException(status_code=404, detail="Deserter case not found.")
        if case["target_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Only the reported person can respond to this case.")
        if case["status"] in ("dismissed", "upheld"):
            raise HTTPException(status_code=409, detail="This case has already been reviewed by an administrator.")
        conn.execute("UPDATE deserter_cases SET response_text=?,status='disputed',updated_at=? WHERE id=?", (data.response.strip(), now_iso(), case_id))
        _log(conn, case["team_id"], user["id"], "Trust & Review Agent", "Recorded the reported person's response", {"case_id": case_id, "status": "disputed"})
        for admin_row in conn.execute("SELECT id FROM users WHERE is_admin=1").fetchall():
            _message(conn, admin_row["id"], "Deserter case disputed", f"The reported person submitted a response for {case['event_name']}. Review the response and reports.", msg_type="admin_review", data={"case_id": case_id})
        conn.commit()
        return {"ok": True, "message": "Your response has been saved and the case is marked disputed for review."}
    finally:
        conn.close()

@app.get("/api/admin/deserter-cases")
def admin_deserter_cases(user: dict = Depends(current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    conn = connect()
    try:
        rows = conn.execute("SELECT c.*,u.name AS target_name,u.email AS target_email,t.name AS team_name FROM deserter_cases c JOIN users u ON u.id=c.target_id JOIN teams t ON t.id=c.team_id ORDER BY CASE c.status WHEN 'pending' THEN 0 WHEN 'disputed' THEN 1 WHEN 'under_review' THEN 2 ELSE 3 END,c.updated_at DESC").fetchall()
        result = []
        for r in rows:
            reporters = conn.execute("SELECT dr.id,dr.reporter_id,u.name AS reporter_name,dr.explanation,dr.status,dr.created_at FROM deserter_reports dr JOIN users u ON u.id=dr.reporter_id WHERE dr.team_id=? AND dr.event_id=? AND dr.target_id=? ORDER BY dr.created_at", (r["team_id"],r["event_id"],r["target_id"])).fetchall()
            result.append({"id":r["id"],"event_name":r["event_name"],"team_name":r["team_name"],"target_id":r["target_id"],"target_name":r["target_name"],"target_email":r["target_email"],"report_count":r["report_count"],"required_threshold":r["required_threshold"],"status":r["status"],"response_text":r["response_text"],"moderator_note":r["moderator_note"],"created_at":r["created_at"],"reports":[dict(x) for x in reporters]})
        return {"cases": result}
    finally:
        conn.close()

@app.post("/api/admin/deserter-cases/{case_id}/review")
def review_deserter_case(case_id: int, data: AdminCaseReviewIn, user: dict = Depends(current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    conn = connect()
    try:
        case = conn.execute("SELECT * FROM deserter_cases WHERE id=?", (case_id,)).fetchone()
        if not case:
            raise HTTPException(status_code=404, detail="Deserter case not found.")
        status = {"uphold":"upheld", "dismiss":"dismissed", "review":"under_review"}[data.decision]
        conn.execute("UPDATE deserter_cases SET status=?,moderator_note=?,updated_at=? WHERE id=?", (status, data.note.strip(), now_iso(), case_id))
        _log(conn, case["team_id"], user["id"], "Admin Review", "Reviewed possible-deserter case", {"case_id": case_id, "decision": data.decision, "status": status})
        _message(conn, case["target_id"], "Your team-reliability report was reviewed", f"The administrator set the case for {case['event_name']} to '{status}'." + (f" Note: {data.note.strip()}" if data.note.strip() else ""), msg_type="deserter_case_update", sender_id=user["id"], data={"case_id": case_id, "status": status})
        conn.commit()
        return {"ok": True, "status": status, "message": "Case decision saved."}
    finally:
        conn.close()

# ------------------------- GitHub repository research -------------------------
CURATED_REPOSITORIES = [
    {"full_name":"fastapi/fastapi","name":"FastAPI","owner":"fastapi","description":"A modern, high-performance Python web framework for building APIs.","language":"Python","html_url":"https://github.com/fastapi/fastapi","license":"MIT","topics":["api","python","backend"],"source":"Curated offline fallback"},
    {"full_name":"huggingface/transformers","name":"Transformers","owner":"huggingface","description":"Machine learning models and tools for natural language, vision, audio and multimodal tasks.","language":"Python","html_url":"https://github.com/huggingface/transformers","license":"Apache-2.0","topics":["ai","machine-learning","nlp"],"source":"Curated offline fallback"},
    {"full_name":"streamlit/streamlit","name":"Streamlit","owner":"streamlit","description":"Build data apps in Python with a simple application model.","language":"Python","html_url":"https://github.com/streamlit/streamlit","license":"Apache-2.0","topics":["data","dashboard","python"],"source":"Curated offline fallback"},
    {"full_name":"scikit-learn/scikit-learn","name":"scikit-learn","owner":"scikit-learn","description":"Machine learning tools for Python, including classification, regression, clustering and model selection.","language":"Python","html_url":"https://github.com/scikit-learn/scikit-learn","license":"BSD-3-Clause","topics":["machine-learning","data-science","python"],"source":"Curated offline fallback"},
    {"full_name":"vercel/next.js","name":"Next.js","owner":"vercel","description":"A React framework for building web applications.","language":"TypeScript","html_url":"https://github.com/vercel/next.js","license":"MIT","topics":["react","frontend","web"],"source":"Curated offline fallback"},
    {"full_name":"streamlit/streamlit-example-apps","name":"Streamlit example apps","owner":"streamlit","description":"Example applications that can help teams prototype data-oriented interfaces.","language":"Python","html_url":"https://github.com/streamlit/streamlit-example-apps","license":"Apache-2.0","topics":["examples","prototypes"],"source":"Curated offline fallback"},
    {"full_name":"langchain-ai/langchain","name":"LangChain","owner":"langchain-ai","description":"Framework and tools for building applications with language models.","language":"Python","html_url":"https://github.com/langchain-ai/langchain","license":"MIT","topics":["agents","llm","ai"],"source":"Curated offline fallback"},
    {"full_name":"openai/openai-cookbook","name":"OpenAI Cookbook","owner":"openai","description":"Examples and guides for common tasks using OpenAI APIs.","language":"Jupyter Notebook","html_url":"https://github.com/openai/openai-cookbook","license":"MIT","topics":["llm","examples","ai"],"source":"Curated offline fallback"},
]

@app.get("/api/github/search")
def github_search(q: str, language: str = "", user: dict = Depends(current_user)):
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(status_code=422, detail="Enter at least two characters to search GitHub repositories.")
    terms = query + (f" language:{language.strip()}" if language.strip() else "")
    live = False
    repos = []
    try:
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({"q": terms, "sort": "updated", "order": "desc", "per_page": 12})
        github_headers = {"User-Agent":"CampusConnect-Hackathon-Prototype/1.0", "Accept":"application/vnd.github+json"}
        github_token = os.getenv("GITHUB_TOKEN", "").strip()
        if github_token:
            github_headers["Authorization"] = f"Bearer {github_token}"
        req = urllib.request.Request(url, headers=github_headers)
        with urllib.request.urlopen(req, timeout=7) as resp:
            payload = json.loads(resp.read().decode())
        repos = []
        for item in payload.get("items", []):
            lic = item.get("license") or {}
            repos.append({"full_name":item.get("full_name"),"name":item.get("name"),"owner":(item.get("owner") or {}).get("login"),"description":item.get("description") or "No description supplied by the repository owner.","language":item.get("language"),"html_url":item.get("html_url"),"stars":item.get("stargazers_count",0),"updated_at":item.get("updated_at"),"license":lic.get("spdx_id"),"topics":item.get("topics",[]),"fork":bool(item.get("fork")),"source":"GitHub public API"})
        live = True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, KeyError):
        query_tokens = set(re.findall(r"[a-zA-Z0-9+#.]{2,}", (query + " " + language).lower()))
        def relevance(repo):
            text = " ".join([repo.get("full_name", ""),repo.get("description", ""),repo.get("language") or "", " ".join(repo.get("topics", []))]).lower()
            return sum(1 for t in query_tokens if t in text)
        repos = sorted(CURATED_REPOSITORIES, key=relevance, reverse=True)[:8]
        repos = [dict(r, match_hint="Curated offline example; metadata may be incomplete or stale.") for r in repos if relevance(r) > 0] or [dict(r, match_hint="Curated offline example; metadata may be incomplete or stale.") for r in CURATED_REPOSITORIES[:5]]
    # The LLM may explain search results, but it must not invent repo metadata. Deterministic ranking is enough here.
    for repo in repos:
        text = " ".join([repo.get("full_name", ""), repo.get("description", ""), repo.get("language") or "", " ".join(repo.get("topics") or [])]).lower()
        tokens = set(re.findall(r"[a-zA-Z0-9+#.]{2,}", query.lower()))
        matched = [t for t in tokens if t in text]
        repo["relevance_score"] = min(100, 45 + len(matched) * 12)
        repo["why_relevant"] = "Matches query terms: " + ", ".join(matched[:4]) if matched else "Related by repository description or topic; review the README before choosing."
    conn = connect()
    try:
        _log(conn, None, user["id"], "GitHub Research Agent", "Searched repository metadata", {"query": query, "language": language, "live_api": live, "results": len(repos)})
        conn.commit()
    finally:
        conn.close()
    return {"query": query, "repositories": repos, "source": "GitHub public API" if live else "Curated offline fallback", "live": live, "notice": "Repository metadata is retrieved from GitHub." if live else "GitHub could not be reached from this server. These curated examples are fallback suggestions, not live search results."}

@app.get("/api/github/my-evidence")
def my_github_evidence(user: dict = Depends(current_user)):
    conn = connect()
    try:
        row = conn.execute("SELECT github_snapshot,github_username FROM users WHERE id=?", (user["id"],)).fetchone()
        return {"username": row["github_username"], "snapshot": safe_json(row["github_snapshot"], {})}
    finally:
        conn.close()

# ------------------------- privacy / blocks -------------------------
@app.post("/api/users/{target_id}/block")
def block_user(target_id: int, user: dict = Depends(current_user)):
    if target_id == user["id"]:
        raise HTTPException(status_code=400, detail="You cannot block your own account.")
    conn = connect()
    try:
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (target_id,)).fetchone():
            raise HTTPException(status_code=404, detail="User not found.")
        conn.execute("INSERT OR IGNORE INTO blocks(blocker_id,blocked_id,created_at) VALUES(?,?,?)", (user["id"], target_id, now_iso()))
        # Close pending direct requests between the two parties so they do not keep resurfacing.
        conn.execute("UPDATE requests SET status='cancelled',responded_at=? WHERE status='pending' AND ((from_user_id=? AND to_user_id=?) OR (from_user_id=? AND to_user_id=?))", (now_iso(), user["id"], target_id, target_id, user["id"]))
        conn.commit()
        return {"ok": True, "message": "This user is blocked from future teammate recommendations and requests."}
    finally:
        conn.close()

@app.delete("/api/users/{target_id}/block")
def unblock_user(target_id: int, user: dict = Depends(current_user)):
    conn = connect()
    try:
        conn.execute("DELETE FROM blocks WHERE blocker_id=? AND blocked_id=?", (user["id"], target_id))
        conn.commit()
        return {"ok": True, "message": "Block removed."}
    finally:
        conn.close()

# ------------------------- admin / demo support -------------------------
@app.get("/api/admin/event-proposals")
def admin_event_proposals(user: dict = Depends(current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    conn = connect()
    try:
        rows = conn.execute("SELECT ep.*,u.name AS proposer_name FROM event_proposals ep JOIN users u ON u.id=ep.proposer_id ORDER BY ep.created_at DESC").fetchall()
        return {"proposals": [dict(r) for r in rows]}
    finally:
        conn.close()

@app.post("/api/admin/event-proposals/{proposal_id}/accept")
def accept_event_proposal(proposal_id: int, user: dict = Depends(current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    conn = connect()
    try:
        proposal = conn.execute("SELECT * FROM event_proposals WHERE id=?", (proposal_id,)).fetchone()
        if not proposal:
            raise HTTPException(status_code=404, detail="Event proposal not found.")
        conn.execute("UPDATE event_proposals SET status='reviewed' WHERE id=?", (proposal_id,))
        _message(conn, proposal["proposer_id"], "Event suggestion reviewed", f"Your suggested event '{proposal['name']}' has been reviewed. An administrator will add verified details if suitable.", msg_type="event_proposal_update", sender_id=user["id"], data={"proposal_id": proposal_id})
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@app.get("/api/admin/people")
def admin_people(user: dict = Depends(current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    conn = connect()
    try:
        rows = conn.execute("SELECT id,email,name,college,course,year,city,bio,profile_goal,skills,interests,roles,availability,work_preferences,github_username,github_url,linkedin_url,portfolio_url,projects,hackathons,discoverable,is_demo,created_at FROM users WHERE is_admin=0 ORDER BY created_at DESC LIMIT 250").fetchall()
        people = []
        for row in rows:
            person = dict(row)
            for field in ("skills", "interests", "roles", "availability", "work_preferences", "projects", "hackathons"):
                person[field] = safe_json(person.get(field), [])
            person["discoverable"] = bool(person["discoverable"])
            person["is_demo"] = bool(person["is_demo"])
            person["overall_profile_score"] = calculate_profile_score(conn, person["id"])
            people.append(person)
        return {"people": people, "count": len(people), "demo_mode": DEMO_MODE}
    finally:
        conn.close()


@app.post("/api/admin/people")
def admin_create_person(data: AdminPersonIn, user: dict = Depends(current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    email = data.email.strip().lower()
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    github_username = data.github_username.strip()
    github_url = data.github_url.strip()
    if not github_username and github_url:
        parsed_github = urllib.parse.urlparse(github_url)
        parts = [part for part in parsed_github.path.split("/") if part]
        if parsed_github.hostname and parsed_github.hostname.lower() in {"github.com", "www.github.com"} and parts:
            if parts[0] not in {"settings", "features", "topics", "orgs", "login", "signup"}:
                github_username = parts[0]
    if github_username and not re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$", github_username):
        raise HTTPException(status_code=422, detail="Enter a valid GitHub username.")
    for field, value in (("github_url", github_url), ("linkedin_url", data.linkedin_url), ("portfolio_url", data.portfolio_url)):
        if value and not value.startswith(("https://", "http://")):
            raise HTTPException(status_code=422, detail=f"{field} must be a complete http(s) URL.")
    conn = connect()
    try:
        ts = now_iso()
        try:
            cur = conn.execute(
                """INSERT INTO users(email,password_hash,name,college,course,year,bio,city,skills,interests,roles,availability,work_preferences,profile_goal,github_username,github_url,linkedin_url,portfolio_url,projects,hackathons,discoverable,preferred_mode,travel_radius,is_admin,is_demo,notification_preference,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'Either',25,0,?,'in_app',?,?)""",
                (email, hash_password(data.password), data.name.strip(), data.college.strip(), data.course.strip(), data.year.strip(), data.bio.strip(), data.city.strip(), dumps([x.strip() for x in data.skills if x.strip()]), dumps([x.strip() for x in data.interests if x.strip()]), dumps([x.strip() for x in data.roles if x.strip()]), dumps([x.strip() for x in data.availability if x.strip()]), dumps([x.strip() for x in data.work_preferences if x.strip()]), data.profile_goal.strip(), data.github_username.strip(), data.github_url.strip(), data.linkedin_url.strip(), data.portfolio_url.strip(), dumps(data.projects), dumps(data.hackathons), int(data.discoverable), int(DEMO_MODE), ts, ts),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=409, detail="An account with this email already exists.")
            raise
        person_id = int(cur.lastrowid)
        _message(conn, person_id, "Your Campus Connect account is ready", "An administrator created your account for Campus Connect. Sign in using the email and temporary password that were securely shared with you, then review your profile.", msg_type="system")
        _log(conn, None, user["id"], "Admin & Trust", "Added a customizable candidate account", {"person_id": person_id, "demo_profile": DEMO_MODE})
        conn.commit()
        # Best-effort snapshot for the admin's supplied GitHub username. GitHub errors never
        # prevent account creation; the candidate can also trigger refresh by saving their profile.
        if github_username:
            snapshot = _fetch_github_snapshot(github_username)
            if snapshot:
                conn.execute("UPDATE users SET github_snapshot=?,updated_at=? WHERE id=?", (dumps(snapshot), now_iso(), person_id))
                conn.commit()
        return {"ok": True, "person": _profile_dict(conn, person_id, include_private=True), "message": "Candidate account created. The person can sign in and accept or decline invitations with their own account."}
    finally:
        conn.close()


@app.get("/api/admin/overview")
def admin_overview(user: dict = Depends(current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    conn = connect()
    try:
        return {"users":conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],"events":conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],"teams":conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0],"pending_cases":conn.execute("SELECT COUNT(*) FROM deserter_cases WHERE status IN ('pending','disputed','under_review')").fetchone()[0],"pending_event_proposals":conn.execute("SELECT COUNT(*) FROM event_proposals WHERE status='pending'").fetchone()[0]}
    finally:
        conn.close()

@app.post("/api/admin/reset-demo")
def reset_demo(user: dict = Depends(current_user)):
    if not user["is_admin"] or not DEMO_MODE:
        raise HTTPException(status_code=403, detail="Demo reset is available only in local demo mode to admins.")
    # Avoid deleting the currently-used database in place; users can restart with a fresh DB file by following README.
    raise HTTPException(status_code=501, detail="For safety, reset demo data by stopping the server, deleting data/campus_connect.sqlite3, and restarting. See README.")

# ------------------------- fallback error handling -------------------------
@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    # Do not leak SQL errors, secrets, or stack traces to the browser. Server logs retain details.
    print(f"[Campus Connect] Unhandled error on {request.method} {request.url.path}: {type(exc).__name__}: {exc}")
    return JSONResponse(status_code=500, content={"detail":"An unexpected server error occurred. Check the terminal logs for diagnostics."})
