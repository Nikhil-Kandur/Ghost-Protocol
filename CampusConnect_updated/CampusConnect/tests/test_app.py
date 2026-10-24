"""Integration smoke tests for the Campus Connect demo backend.

Run with: CAMPUS_DB_PATH=/tmp/campus-connect-tests.sqlite3 pytest -q
"""
import os
from pathlib import Path
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("CAMPUS_DB_PATH", "/tmp/campus-connect-pytest.sqlite3")
try:
    Path(os.environ["CAMPUS_DB_PATH"]).unlink(missing_ok=True)
except Exception:
    pass
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def login(email):
    r = client.post("/api/auth/login", json={"email": email, "password": "Campus123!"})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def test_health_and_demo_login():
    assert client.get("/api/health").json()["ok"] is True
    u = login("captain@campusconnect.demo")
    assert u["name"] == "Olivia Chen"
    assert "overall_profile_score" in u
    assert "compatibility_score" not in u


def test_agent_matching_is_captain_only_and_has_two_scores():
    captain = login("captain@campusconnect.demo")
    response = client.post("/api/teams/1/match", json={"target_role":"primary","recovery":False})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["run_id"]
    assert "Backend" in data["missing_skills"]
    assert data["candidates"]
    assert all("compatibility" in c and "profile_score" in c["user"] for c in data["candidates"])
    # Candidate profile endpoint has the global profile score but never a team compatibility score.
    candidate_id = data["candidates"][0]["user"]["id"]
    p = client.get(f"/api/profile/{candidate_id}")
    assert p.status_code == 200
    assert "overall_profile_score" in p.json()["profile"]
    assert "compatibility_score" not in p.json()["profile"]
    # Non-captain cannot execute or inspect a captain's matching run.
    login("arjun@campusconnect.demo")
    denied = client.post("/api/teams/1/match", json={"target_role":"primary"})
    assert denied.status_code == 403
    denied_run = client.get(f"/api/agent/runs/{data['run_id']}")
    assert denied_run.status_code == 403



def test_notification_preference_persists_and_inbox_remains_default_channel():
    login("newbie@campusconnect.demo")
    changed = client.patch("/api/profile", json={"notification_preference":"in_app_email"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["user"]["notification_preference"] == "in_app_email"
    assert "email_delivery_configured" in changed.json()["user"]
    reset = client.patch("/api/profile", json={"notification_preference":"in_app"})
    assert reset.status_code == 200, reset.text
    assert reset.json()["user"]["notification_preference"] == "in_app"
    assert client.get("/api/inbox").status_code == 200

def test_invitation_acceptance_and_one_backup_limit():
    login("captain@campusconnect.demo")
    invite = client.post("/api/teams/1/requests", json={"candidate_id":2,"role":"primary","role_label":"Backend developer","message":"Fill backend gap"})
    assert invite.status_code == 200, invite.text
    request_id = invite.json()["request_id"]
    login("arjun@campusconnect.demo")
    accepted = client.post(f"/api/requests/{request_id}/respond", json={"action":"accept"})
    assert accepted.status_code == 200, accepted.text
    team = client.get("/api/teams/1").json()["team"]
    assert team["primary_count"] == 3
    # Captain can invite exactly one backup.
    login("captain@campusconnect.demo")
    invite_backup = client.post("/api/teams/1/requests", json={"candidate_id":4,"role":"backup","role_label":"AI/ML reserve"})
    assert invite_backup.status_code == 200, invite_backup.text
    second_backup = client.post("/api/teams/1/requests", json={"candidate_id":6,"role":"backup","role_label":"Second reserve"})
    assert second_backup.status_code == 409
    login("karthik@campusconnect.demo")
    request_backup_id = invite_backup.json()["request_id"]
    accepted = client.post(f"/api/requests/{request_backup_id}/respond", json={"action":"accept"})
    assert accepted.status_code == 200, accepted.text
    team_data = client.get("/api/teams/1").json()
    assert team_data["team"]["backup_count"] == 1
    assert team_data["team"]["backup"]["name"] == "Karthik Shah"

    # A declined invitation triggers an actual recovery workflow and the same
    # candidate is not repeatedly presented for the same role/team.
    login("captain@campusconnect.demo")
    invite_leena = client.post("/api/teams/1/requests", json={"candidate_id":6,"role":"primary","role_label":"AI/ML contributor"})
    assert invite_leena.status_code == 200, invite_leena.text
    login("leena@campusconnect.demo")
    declined = client.post(f"/api/requests/{invite_leena.json()['request_id']}/respond", json={"action":"decline"})
    assert declined.status_code == 200, declined.text
    assert declined.json()["recovery_run_id"]
    login("captain@campusconnect.demo")
    recovered = client.get(f"/api/agent/runs/{declined.json()['recovery_run_id']}")
    candidate_ids = [c["user"]["id"] for c in recovered.json()["run"]["result"]["candidates"]]
    assert 6 not in candidate_ids


def test_backup_promotion_updates_roster_and_frees_slot():
    # A prior test has joined Arjun and Karthik as backup. Maya withdraws from team 1.
    login("maya@campusconnect.demo")
    withdraw = client.post("/api/teams/1/withdraw", json={"reason":"Test withdrawal"})
    assert withdraw.status_code == 200, withdraw.text
    login("captain@campusconnect.demo")
    promo = client.post("/api/teams/1/promote-backup")
    assert promo.status_code == 200, promo.text
    request_id = promo.json()["request_id"]
    login("karthik@campusconnect.demo")
    accepted = client.post(f"/api/requests/{request_id}/respond", json={"action":"accept"})
    assert accepted.status_code == 200, accepted.text
    team = client.get("/api/teams/1").json()
    assert team["team"]["backup_count"] == 0
    roles = {m["profile"]["name"]:m["role"] for m in team["members"]}
    assert roles["Karthik Shah"] == "primary"
    assert team["team"]["backup_slot_available"] is True


def test_deserter_threshold_requires_two_distinct_reporters_and_can_be_disputed():
    # Event 3 is a completed synthetic event and the target is Rhea (user id 5).
    login("captain@campusconnect.demo")
    profile_before = client.get("/api/profile/5").json()["profile"]["overall_profile_score"]["score"]
    initial_match = client.post("/api/teams/1/match", json={"target_role":"primary"})
    assert initial_match.status_code == 200
    rhea_initial = next(c for c in initial_match.json()["candidates"] if c["user"]["id"] == 5)
    compatibility_before = rhea_initial["compatibility"]["score"]
    r1 = client.post("/api/teams/3/deserter-reports", json={"target_id":5,"explanation":"The teammate accepted the event-day commitment but was absent with no prior notice."})
    assert r1.status_code == 200, r1.text
    assert r1.json()["case"]["tag_active"] is False
    login("arjun@campusconnect.demo")
    r2 = client.post("/api/teams/3/deserter-reports", json={"target_id":5,"explanation":"The teammate confirmed they would attend, did not appear on the agreed day, and did not contact the team."})
    assert r2.status_code == 200, r2.text
    assert r2.json()["case"]["tag_active"] is True
    # Pending allegations cannot change technical/general profile score or the
    # numeric compatibility score; they are visible as contextual warnings only.
    profile_after = client.get("/api/profile/5").json()["profile"]
    assert profile_after["overall_profile_score"]["score"] == profile_before
    assert "compatibility_score" not in profile_after
    login("captain@campusconnect.demo")
    match_after = client.post("/api/teams/1/match", json={"target_role":"primary"})
    rhea_after = next(c for c in match_after.json()["candidates"] if c["user"]["id"] == 5)
    assert rhea_after["compatibility"]["score"] == compatibility_before
    assert any(w["label"] == "Possible Deserter" for w in rhea_after["warnings"])
    login("rhea@campusconnect.demo")
    profile = client.get("/api/me").json()["user"]
    assert profile["deserter_tags"] and profile["deserter_tags"][0]["label"] == "Possible Deserter"
    case_id = profile["deserter_tags"][0]["case_id"]
    appeal = client.post(f"/api/deserter-cases/{case_id}/respond", json={"response":"I dispute this and want the event-day circumstances reviewed before drawing a conclusion."})
    assert appeal.status_code == 200, appeal.text
    profile = client.get("/api/me").json()["user"]
    assert profile["deserter_tags"][0]["status"] == "disputed"


def test_admin_can_review_and_remove_case_tag():
    # The previous test created a disputed case. Admin dismissal removes the visible tag.
    login("admin@campusconnect.demo")
    cases = client.get("/api/admin/deserter-cases").json()["cases"]
    assert cases
    case_id = cases[0]["id"]
    review = client.post(f"/api/admin/deserter-cases/{case_id}/review", json={"decision":"dismiss","note":"Demo test: report not substantiated."})
    assert review.status_code == 200, review.text
    login("rhea@campusconnect.demo")
    profile = client.get("/api/me").json()["user"]
    assert profile["deserter_tags"] == []


def test_github_search_returns_live_or_explicit_offline_fallback():
    login("captain@campusconnect.demo")
    response = client.get("/api/github/search?q=fastapi+agent+orchestration")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["repositories"]
    assert data["source"] in ("GitHub public API", "Curated offline fallback")
    assert "notice" in data


def test_inbox_and_event_proposal_are_persisted():
    login("captain@campusconnect.demo")
    inbox = client.get("/api/inbox")
    assert inbox.status_code == 200
    proposal = client.post("/api/event-proposals", json={"name":"Test hackathon proposal","url":"https://example.org/event","reason":"Useful test proposal from an integration test."})
    assert proposal.status_code == 200, proposal.text
    login("admin@campusconnect.demo")
    pending = client.get("/api/admin/event-proposals").json()["proposals"]
    assert any(p["name"] == "Test hackathon proposal" for p in pending)


def test_withdrawn_primary_member_cannot_be_reported_as_a_deserter():
    from app.db import connect
    conn = connect()
    try:
        # Maya is a primary on the completed demo team; simulate a recorded withdrawal.
        conn.execute("UPDATE memberships SET status='withdrawn',withdrawn_at=CURRENT_TIMESTAMP WHERE team_id=3 AND user_id=3")
        conn.commit()
    finally:
        conn.close()
    login("captain@campusconnect.demo")
    response = client.post("/api/teams/3/deserter-reports", json={"target_id":3,"explanation":"This user withdrew from the team before event day."})
    assert response.status_code == 400
    assert "withdrew from the team" in response.json()["detail"]


def test_multiple_independent_accounts_can_register_and_sign_in():
    """Registration is open to additional unique accounts, not demo-only."""
    users = [
        {"name": "Multi Account One", "email": "multi.one@example.test", "password": "UniquePass123!", "college": "Example College", "city": "Chennai"},
        {"name": "Multi Account Two", "email": "multi.two@example.test", "password": "UniquePass456!", "college": "Example College", "city": "Bengaluru"},
    ]
    for body in users:
        response = client.post("/api/auth/register", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["user"]["email"] == body["email"]
        assert "overall_profile_score" in response.json()["user"]
    # The same email is not duplicated, but a different user can sign in with their own credentials.
    duplicate = client.post("/api/auth/register", json={**users[0], "name": "Duplicate Name"})
    assert duplicate.status_code == 409
    second_login = client.post("/api/auth/login", json={"email": users[1]["email"], "password": users[1]["password"]})
    assert second_login.status_code == 200, second_login.text
    assert client.get("/api/me").json()["user"]["email"] == users[1]["email"]


def test_github_profile_url_refreshes_evidence_automatically(monkeypatch):
    import app.main as main_module
    login("newbie@campusconnect.demo")
    monkeypatch.setattr(main_module, "_fetch_github_snapshot", lambda username: {
        "synced_at": "2026-10-24T00:00:00+00:00", "username": username,
        "public_repos": 8, "recent_repos": 3, "described_repos": 6,
        "repos": [{"name": f"{username}/sample", "description": "Public test repo", "language": "Python", "url": f"https://github.com/{username}/sample"}],
        "source": "GitHub public REST API", "profile_url": f"https://github.com/{username}"
    })
    response = client.patch("/api/profile", json={"github_url": "https://github.com/profileevidence"})
    assert response.status_code == 200, response.text
    profile = response.json()["user"]
    assert profile["github_username"] == "profileevidence"
    assert profile["github_synced"] is True
    component = next(c for c in profile["overall_profile_score"]["components"] if c["key"] == "github_evidence")
    assert "8 public repos" in component["evidence"]


def test_admin_can_add_customizable_candidate_account_and_candidate_can_login():
    login("admin@campusconnect.demo")
    response = client.post("/api/admin/people", json={
        "name": "Custom Match Candidate", "email": "custom.match@example.test", "password": "CandidatePass123!",
        "college": "Custom College", "course": "Computer Science", "year": "2nd year", "city": "Chennai",
        "bio": "A candidate created through the admin profile builder.", "profile_goal": "Build APIs for hackathons",
        "skills": ["Backend", "Python", "REST APIs"], "interests": ["AI", "developer tools"],
        "roles": ["Backend developer"], "availability": ["weekends"],
        "work_preferences": ["collaborative"], "discoverable": True
    })
    assert response.status_code == 200, response.text
    person_id = response.json()["person"]["id"]
    login_response = client.post("/api/auth/login", json={"email": "custom.match@example.test", "password": "CandidatePass123!"})
    assert login_response.status_code == 200, login_response.text
    me = client.get("/api/me").json()["user"]
    assert me["id"] == person_id
    assert "Backend" in me["skills"]


def test_team_architect_bulk_invites_create_separate_inbox_requests():
    # Create three independent real accounts so this test doesn't depend on the prior
    # state of a demo team's membership or the pre-seeded candidate availability.
    user_bodies = [
        {"name": "Bulk Captain", "email": "bulk.captain@example.test", "password": "BulkCaptain123!", "college": "Example College", "city": "Chennai"},
        {"name": "Bulk Backend", "email": "bulk.backend@example.test", "password": "BulkBackend123!", "college": "Example College", "city": "Chennai"},
        {"name": "Bulk AI Builder", "email": "bulk.ai@example.test", "password": "BulkAiBuilder123!", "college": "Example College", "city": "Chennai"},
    ]
    for body in user_bodies:
        response = client.post("/api/auth/register", json=body)
        assert response.status_code == 200, response.text
    # Save customized candidate profiles before matching.
    client.post("/api/auth/login", json={"email": user_bodies[1]["email"], "password": user_bodies[1]["password"]})
    client.patch("/api/profile", json={"skills": ["Backend", "Python", "REST APIs"], "roles": ["Backend developer"], "availability": ["weekends"], "discoverable": True})
    client.post("/api/auth/login", json={"email": user_bodies[2]["email"], "password": user_bodies[2]["password"]})
    client.patch("/api/profile", json={"skills": ["AI/ML", "Python", "Data analysis"], "roles": ["AI/ML engineer"], "availability": ["weekends"], "discoverable": True})
    client.post("/api/auth/login", json={"email": user_bodies[0]["email"], "password": user_bodies[0]["password"]})
    team = client.post("/api/teams", json={"event_id": 1, "name": "Bulk Invite Test Team", "goal": "Build an AI hackathon prototype"})
    assert team.status_code == 200, team.text
    team_id = team.json()["team"]["id"]
    match = client.post(f"/api/teams/{team_id}/match", json={"target_role": "primary"})
    assert match.status_code == 200, match.text
    data = match.json()
    assert data["suggested_candidate_ids"]
    invited = client.post(f"/api/teams/{team_id}/requests/bulk", json={
        "candidate_ids": data["suggested_candidate_ids"], "run_id": data["run_id"], "role": "primary"
    })
    assert invited.status_code == 200, invited.text
    assert invited.json()["invited_count"] >= 1
    assert all(x["request_id"] for x in invited.json()["invited"])
    # Each invite is an individual request/inbox message. Recipients must log in to respond.
    first_candidate = invited.json()["invited"][0]
    from app.db import connect
    conn = connect()
    try:
        row = conn.execute("SELECT email FROM users WHERE id=?", (first_candidate["candidate_id"],)).fetchone()
        assert row
    finally:
        conn.close()


def test_sarvam_provider_settings_use_server_side_key_and_v2_endpoint(monkeypatch):
    from app import agents
    monkeypatch.setenv("AI_PROVIDER", "sarvam")
    monkeypatch.setenv("SARVAM_API_KEY", "test-key-never-used-for-network")
    monkeypatch.setenv("SARVAM_MODEL", "sarvam-105b")
    monkeypatch.setenv("SARVAM_BASE_URL", "https://api.sarvam.ai/v2")
    assert agents.llm_provider() == "sarvam"
    settings = agents._llm_settings()
    assert settings is not None
    provider, key, model, headers, base = settings
    assert provider == "sarvam"
    assert key == "test-key-never-used-for-network"
    assert model == "sarvam-105b"
    assert headers["api-subscription-key"] == "test-key-never-used-for-network"
    assert base.rstrip("/") + "/chat/completions" == "https://api.sarvam.ai/v2/chat/completions"
