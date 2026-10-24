"""SQLite persistence and demo seed data for Campus Connect."""
from __future__ import annotations
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("CAMPUS_DATA_DIR", str(ROOT / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("CAMPUS_DB_PATH", str(DATA_DIR / "campus_connect.sqlite3")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240_000)
    return salt.hex() + "$" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        return secrets.compare_digest(hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 240_000).hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    college TEXT NOT NULL DEFAULT '',
    course TEXT NOT NULL DEFAULT '',
    year TEXT NOT NULL DEFAULT '',
    bio TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    skills TEXT NOT NULL DEFAULT '[]',
    interests TEXT NOT NULL DEFAULT '[]',
    roles TEXT NOT NULL DEFAULT '[]',
    availability TEXT NOT NULL DEFAULT '[]',
    work_preferences TEXT NOT NULL DEFAULT '[]',
    profile_goal TEXT NOT NULL DEFAULT '',
    github_username TEXT NOT NULL DEFAULT '',
    github_url TEXT NOT NULL DEFAULT '',
    linkedin_url TEXT NOT NULL DEFAULT '',
    portfolio_url TEXT NOT NULL DEFAULT '',
    projects TEXT NOT NULL DEFAULT '[]',
    hackathons TEXT NOT NULL DEFAULT '[]',
    github_snapshot TEXT NOT NULL DEFAULT '{}',
    discoverable INTEGER NOT NULL DEFAULT 1,
    preferred_mode TEXT NOT NULL DEFAULT 'Either',
    travel_radius INTEGER NOT NULL DEFAULT 25,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_demo INTEGER NOT NULL DEFAULT 0,
    notification_preference TEXT NOT NULL DEFAULT 'in_app',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    organizer TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'Hackathon',
    mode TEXT NOT NULL DEFAULT 'In person',
    location TEXT NOT NULL DEFAULT '',
    event_start TEXT NOT NULL,
    event_end TEXT NOT NULL,
    registration_deadline TEXT NOT NULL,
    min_team_size INTEGER NOT NULL DEFAULT 2,
    max_team_size INTEGER NOT NULL DEFAULT 4,
    required_skills TEXT NOT NULL DEFAULT '[]',
    eligibility TEXT NOT NULL DEFAULT 'Open to college students',
    registration_url TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    source_label TEXT NOT NULL DEFAULT 'Demo data',
    last_verified_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'upcoming',
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    name TEXT NOT NULL,
    captain_id INTEGER NOT NULL REFERENCES users(id),
    primary_limit INTEGER NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'recruiting',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('primary','backup')),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','withdrawn')),
    role_label TEXT NOT NULL DEFAULT '',
    joined_at TEXT NOT NULL,
    withdrawn_at TEXT NOT NULL DEFAULT '',
    UNIQUE(team_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_memberships_team ON memberships(team_id,status,role);
CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id,status);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    from_user_id INTEGER NOT NULL REFERENCES users(id),
    to_user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL CHECK(kind IN ('invite','application','promotion')),
    offered_role TEXT NOT NULL CHECK(offered_role IN ('primary','backup','promote_backup')),
    role_label TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','declined','cancelled','expired')),
    created_at TEXT NOT NULL,
    responded_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_requests_to_user ON requests(to_user_id,status,created_at);
CREATE INDEX IF NOT EXISTS idx_requests_team ON requests(team_id,status);
CREATE TABLE IF NOT EXISTS event_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('saved','interested','team_search')),
    created_at TEXT NOT NULL,
    UNIQUE(user_id,event_id,kind)
);
CREATE TABLE IF NOT EXISTS blocks (
    blocker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    blocked_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY(blocker_id,blocked_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sender_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_recipient ON messages(recipient_id,is_read,created_at DESC);
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    result TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES agent_runs(id) ON DELETE CASCADE,
    team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    agent TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_events_team ON agent_events(team_id,id DESC);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    event_id INTEGER NOT NULL REFERENCES events(id),
    reviewer_id INTEGER NOT NULL REFERENCES users(id),
    target_id INTEGER NOT NULL REFERENCES users(id),
    contribution INTEGER NOT NULL CHECK(contribution BETWEEN 1 AND 5),
    reliability INTEGER NOT NULL CHECK(reliability BETWEEN 1 AND 5),
    teamwork INTEGER NOT NULL CHECK(teamwork BETWEEN 1 AND 5),
    communication INTEGER NOT NULL CHECK(communication BETWEEN 1 AND 5),
    role_fulfilment INTEGER NOT NULL CHECK(role_fulfilment BETWEEN 1 AND 5),
    comment TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','hidden','removed')),
    created_at TEXT NOT NULL,
    UNIQUE(event_id,reviewer_id,target_id)
);
CREATE TABLE IF NOT EXISTS deserter_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    event_id INTEGER NOT NULL REFERENCES events(id),
    reporter_id INTEGER NOT NULL REFERENCES users(id),
    target_id INTEGER NOT NULL REFERENCES users(id),
    explanation TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'valid' CHECK(status IN ('valid','invalid')),
    created_at TEXT NOT NULL,
    UNIQUE(event_id,reporter_id,target_id)
);
CREATE TABLE IF NOT EXISTS deserter_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    event_id INTEGER NOT NULL REFERENCES events(id),
    target_id INTEGER NOT NULL REFERENCES users(id),
    event_name TEXT NOT NULL,
    report_count INTEGER NOT NULL DEFAULT 0,
    required_threshold INTEGER NOT NULL DEFAULT 2,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','disputed','upheld','dismissed','under_review')),
    response_text TEXT NOT NULL DEFAULT '',
    moderator_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(event_id,team_id,target_id)
);
CREATE TABLE IF NOT EXISTS event_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposer_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);
"""


def _insert_user(conn, email, name, password, *, college="Campus Connect Demo University", course="Computer Science", year="2nd year", bio="", city="Chennai", skills=None, interests=None, roles=None, availability=None, work_preferences=None, goal="", github_username="", github_url="", linkedin_url="", portfolio_url="", projects=None, hackathons=None, admin=False, demo=True):
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO users(email,password_hash,name,college,course,year,bio,city,skills,interests,roles,availability,work_preferences,profile_goal,github_username,github_url,linkedin_url,portfolio_url,projects,hackathons,discoverable,preferred_mode,travel_radius,is_admin,is_demo,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (email, hash_password(password), name, college, course, year, bio, city, dumps(skills or []), dumps(interests or []), dumps(roles or []), dumps(availability or ["weekends"]), dumps(work_preferences or ["collaborative", "async updates"]), goal, github_username, github_url, linkedin_url, portfolio_url, dumps(projects or []), dumps(hackathons or []), 1, "Either", 25, int(admin), int(demo), ts, ts),
    )
    return cur.lastrowid


def _insert_event(conn, *, name, description, organizer, category, mode, location, start, end, deadline, min_size, max_size, skills, status="upcoming", is_demo=True, source_url="", registration_url="", source_label="Demo data"):
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO events(name,description,organizer,category,mode,location,event_start,event_end,registration_deadline,min_team_size,max_team_size,required_skills,eligibility,registration_url,source_url,source_label,last_verified_at,status,is_demo,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (name, description, organizer, category, mode, location, start, end, deadline, min_size, max_size, dumps(skills), "Open to college students; confirm specific event rules with the organiser.", registration_url, source_url, source_label, ts, status, int(is_demo), ts),
    )
    return cur.lastrowid


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        # Lightweight schema migration for databases created by earlier prototype builds.
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "notification_preference" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN notification_preference TEXT NOT NULL DEFAULT 'in_app'")
        # Optional first-run administrator bootstrap for hosted deployments. Configure these
        # secrets in the host dashboard; only the password hash is ever stored.
        admin_email = os.getenv("CAMPUS_ADMIN_EMAIL", "").strip().lower()
        admin_password = os.getenv("CAMPUS_ADMIN_PASSWORD", "")
        admin_name = os.getenv("CAMPUS_ADMIN_NAME", "Campus Connect Admin").strip() or "Campus Connect Admin"
        if bool(admin_email) != bool(admin_password):
            raise RuntimeError("Set both CAMPUS_ADMIN_EMAIL and CAMPUS_ADMIN_PASSWORD to bootstrap the production administrator.")
        if admin_email and len(admin_password) < 12:
            raise RuntimeError("CAMPUS_ADMIN_PASSWORD must contain at least 12 characters.")
        if admin_email and "@" in admin_email:
            admin_row = conn.execute("SELECT id,is_admin FROM users WHERE email=? COLLATE NOCASE", (admin_email,)).fetchone()
            if admin_row:
                if not admin_row["is_admin"]:
                    conn.execute("UPDATE users SET is_admin=1,updated_at=? WHERE id=?", (now_iso(), admin_row["id"]))
                    conn.commit()
            else:
                ts_admin = now_iso()
                conn.execute("INSERT INTO users(email,password_hash,name,college,course,year,bio,city,discoverable,is_admin,is_demo,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,0,1,0,?,?)", (admin_email, hash_password(admin_password), admin_name, "Campus Connect", "Platform operations", "", "Platform administrator", "", ts_admin, ts_admin))
                conn.commit()
        exists = conn.execute("SELECT COUNT(*) FROM users WHERE is_demo=1").fetchone()[0]
        if exists:
            return
        # Demo seed accounts are convenient for a hackathon prototype. Set
        # CAMPUS_SEED_DEMO=false (or CAMPUS_DEMO_MODE=false) before first start
        # when creating a fresh database for a non-demo deployment.
        seed_demo = os.getenv("CAMPUS_SEED_DEMO", os.getenv("CAMPUS_DEMO_MODE", "true")).lower() in ("1", "true", "yes")
        if not seed_demo:
            return
        # All people and events below are synthetic demo data, not real participants or verified live events.
        captain = _insert_user(conn, "captain@campusconnect.demo", "Olivia Chen", "Campus123!", bio="Product-minded builder who enjoys shipping useful prototypes.", city="Chennai", skills=["Frontend", "React", "Product thinking", "Python basics"], interests=["AI", "education", "product design", "hackathons"], roles=["Frontend", "Team lead"], availability=["weekends", "evenings"], work_preferences=["collaborative", "clear milestones", "async updates"], goal="Build practical AI tools with a balanced team", projects=[{"name":"Campus helper prototype","description":"Demo project entry; self-reported sample data.","url":"","evidence_status":"self_reported"}], hackathons=[{"name":"Campus Build Sprint (demo)","outcome":"Participated","verification_status":"self_reported"}])
        arjun = _insert_user(conn, "arjun@campusconnect.demo", "Arjun Rao", "Campus123!", bio="Backend-focused developer who likes APIs and data systems.", city="Chennai", skills=["Backend", "Python", "REST APIs", "SQL"], interests=["AI", "data", "developer tools"], roles=["Backend developer"], availability=["weekends", "evenings"], work_preferences=["collaborative", "clear milestones"], goal="Build reliable backend services", projects=[{"name":"API starter (demo)","description":"Synthetic project profile used for app demonstrations.","url":"","evidence_status":"self_reported"}], hackathons=[{"name":"Campus Build Sprint (demo)","outcome":"Completed prototype","verification_status":"self_reported"}])
        maya = _insert_user(conn, "maya@campusconnect.demo", "Maya Iyer", "Campus123!", bio="Frontend developer and UI/UX enthusiast.", city="Chennai", skills=["Frontend", "React", "UI/UX", "Figma"], interests=["AI", "design", "education"], roles=["Frontend developer", "Designer"], availability=["weekends", "Saturday"], work_preferences=["collaborative", "visual planning"], goal="Make technical ideas intuitive", projects=[{"name":"Student dashboard (demo)","description":"Synthetic project entry.","url":"","evidence_status":"self_reported"}])
        karthik = _insert_user(conn, "karthik@campusconnect.demo", "Karthik Shah", "Campus123!", bio="ML enthusiast with a focus on data-driven prototypes.", city="Chennai", skills=["AI/ML", "Python", "Data analysis", "Model evaluation"], interests=["AI", "healthtech", "climate tech", "data"], roles=["AI/ML engineer"], availability=["weekends", "Sunday"], work_preferences=["collaborative", "research-first"], goal="Turn ML experiments into usable products", projects=[{"name":"Classifier experiment (demo)","description":"Synthetic project entry.","url":"","evidence_status":"self_reported"}], hackathons=[{"name":"Campus Build Sprint (demo)","outcome":"Prototype contributor","verification_status":"self_reported"}])
        rhea = _insert_user(conn, "rhea@campusconnect.demo", "Rhea Das", "Campus123!", bio="Research, pitching, and product storytelling.", city="Chennai", skills=["Research", "Presentation", "Pitching", "Product strategy"], interests=["AI", "entrepreneurship", "education"], roles=["Pitch lead", "Research"], availability=["weekends", "evenings"], work_preferences=["collaborative", "clear milestones", "async updates"], goal="Build a clear story around useful technology", projects=[{"name":"Hackathon pitch deck (demo)","description":"Synthetic project entry.","url":"","evidence_status":"self_reported"}])
        leena = _insert_user(conn, "leena@campusconnect.demo", "Leena Kapoor", "Campus123!", bio="AI/ML and NLP enthusiast.", city="Chennai", skills=["AI/ML", "NLP", "Python", "Research"], interests=["AI", "education", "healthtech"], roles=["AI/ML engineer", "Research"], availability=["weekends", "evenings"], work_preferences=["collaborative", "research-first"], goal="Build useful language technology", projects=[{"name":"NLP prototype (demo)","description":"Synthetic project entry.","url":"","evidence_status":"self_reported"}])
        sam = _insert_user(conn, "sam@campusconnect.demo", "Sam Dev", "Campus123!", bio="Cloud and backend developer.", city="Bengaluru", skills=["Backend", "Cloud", "DevOps", "Security"], interests=["AI", "developer tools", "open source"], roles=["Backend developer", "DevOps"], availability=["weekends", "Friday evening"], work_preferences=["collaborative", "clear milestones"], goal="Build secure and dependable systems")
        newbie = _insert_user(conn, "newbie@campusconnect.demo", "Nila Student", "Campus123!", bio="New to hackathons; keen to learn and contribute.", city="Chennai", skills=["Python basics", "Research"], interests=["AI", "education"], roles=["Research", "Beginner developer"], availability=["weekends"], work_preferences=["mentoring", "collaborative"], goal="Learn by building with a supportive team")
        admin = _insert_user(conn, "admin@campusconnect.demo", "Campus Connect Admin", "Campus123!", college="Campus Connect", course="Platform operations", city="Chennai", skills=["Moderation", "Operations"], interests=["student safety"], roles=["Administrator"], availability=["weekdays"], admin=True)

        event1 = _insert_event(conn, name="Chennai AI Buildathon — Demo Event", description="A sample event to demonstrate agentic teammate discovery, team balancing, invitations, and backup recovery. This is synthetic data, not a verified live event.", organizer="Campus Connect Demo Organisers", category="AI / Hackathon", mode="In person", location="Chennai", start="2026-11-14", end="2026-11-15", deadline="2026-11-09", min_size=3, max_size=4, skills=["Frontend", "Backend", "AI/ML"], source_label="Sample event · demo data")
        event2 = _insert_event(conn, name="Campus Climate Data Sprint — Demo Event", description="A synthetic climate-data challenge for demonstrating event recommendations and mixed-skill team formation.", organizer="Campus Connect Demo Organisers", category="Climate tech", mode="Hybrid", location="Chennai", start="2026-11-28", end="2026-11-29", deadline="2026-11-22", min_size=2, max_size=5, skills=["Data analysis", "Backend", "Presentation"], source_label="Sample event · demo data")
        event3 = _insert_event(conn, name="Campus Build Sprint — Completed Demo Event", description="A completed synthetic event used to demonstrate teammate reviews, reported no-shows, and dispute handling. Not a real event.", organizer="Campus Connect Demo Organisers", category="Campus hackathon", mode="In person", location="Chennai", start="2026-09-12", end="2026-09-13", deadline="2026-09-06", min_size=3, max_size=4, skills=["Frontend", "Backend", "Pitching"], status="completed", source_label="Completed sample event · demo data")
        event4 = _insert_event(conn, name="Remote Open Source Weekend — Demo Event", description="A sample online event focused on practical open-source contributions and developer tooling.", organizer="Campus Connect Demo Organisers", category="Open source", mode="Online", location="Online", start="2026-12-12", end="2026-12-13", deadline="2026-12-08", min_size=2, max_size=4, skills=["Open source", "Backend", "Documentation"], source_label="Sample event · demo data")

        ts = now_iso()
        team1 = conn.execute("INSERT INTO teams(event_id,name,captain_id,primary_limit,goal,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (event1, "Team Nova", captain, 4, "Build a useful AI prototype with clear skill coverage", "recruiting", ts, ts)).lastrowid
        conn.execute("INSERT INTO memberships(team_id,user_id,role,status,role_label,joined_at) VALUES(?,?,?,?,?,?)", (team1, captain, "primary", "active", "Team lead · product/front-end", ts))
        conn.execute("INSERT INTO memberships(team_id,user_id,role,status,role_label,joined_at) VALUES(?,?,?,?,?,?)", (team1, maya, "primary", "active", "Frontend / UX", ts))
        # A second open team lets users discover and apply as primary or backup.
        team2 = conn.execute("INSERT INTO teams(event_id,name,captain_id,primary_limit,goal,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (event2, "Green Signals", arjun, 5, "Build a data product for local climate questions", "recruiting", ts, ts)).lastrowid
        conn.execute("INSERT INTO memberships(team_id,user_id,role,status,role_label,joined_at) VALUES(?,?,?,?,?,?)", (team2, arjun, "primary", "active", "Backend lead", ts))
        conn.execute("INSERT INTO memberships(team_id,user_id,role,status,role_label,joined_at) VALUES(?,?,?,?,?,?)", (team2, rhea, "primary", "active", "Research and pitch", ts))
        # Completed team for review / deserter workflow testing.
        team3 = conn.execute("INSERT INTO teams(event_id,name,captain_id,primary_limit,goal,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (event3, "Prototype Crew", captain, 4, "A completed demo collaboration for review and trust workflows", "completed", ts, ts)).lastrowid
        for uid, label in [(captain, "Team lead"), (arjun, "Backend"), (maya, "Frontend / UX"), (rhea, "Pitch / research")]:
            conn.execute("INSERT INTO memberships(team_id,user_id,role,status,role_label,joined_at) VALUES(?,?,?,?,?,?)", (team3, uid, "primary", "active", label, ts))
        # Seed a few legitimate demo reviews from a completed demo team, but no desertion reports.
        for reviewer, target, vals, comment in [
            (arjun, captain, (4, 5, 5, 4, 4), "Demo feedback: structured the work and kept communication clear."),
            (maya, captain, (4, 4, 5, 4, 4), "Demo feedback: collaborative and organised."),
            (captain, arjun, (5, 4, 4, 4, 5), "Demo feedback: implemented the API work reliably."),
        ]:
            conn.execute("INSERT OR IGNORE INTO reviews(team_id,event_id,reviewer_id,target_id,contribution,reliability,teamwork,communication,role_fulfilment,comment,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (team3, event3, reviewer, target, *vals, comment, "active", ts))
        # Seed event interactions so replacement suggestions can demonstrate opt-in event interest.
        for uid in [karthik, leena, rhea, sam, newbie]:
            conn.execute("INSERT OR IGNORE INTO event_interactions(user_id,event_id,kind,created_at) VALUES(?,?,?,?)", (uid, event1, "interested", ts))
        for uid in [karthik, rhea, sam, newbie]:
            conn.execute("INSERT OR IGNORE INTO event_interactions(user_id,event_id,kind,created_at) VALUES(?,?,?,?)", (uid, event2, "interested", ts))
        # Admin receives a small product onboarding message.
        conn.execute("INSERT INTO messages(recipient_id,type,title,body,data,created_at) VALUES(?,?,?,?,?,?)", (captain, "system", "Welcome to Campus Connect", "Your demo workspace is ready. Try Team Architect, the one-backup workflow, GitHub research, and the completed team's review flow. All people and events with demo labels are synthetic sample data.", dumps({"action":"tour"}), ts))
        conn.commit()
    finally:
        conn.close()
