# Campus Connect

**Campus Connect** is a student-opportunity and team-formation platform built for the Phantom Agents / Agentic AI track. It combines event discovery, private captain-side teammate matching, invitations, a one-backup-member workflow, team recovery, public GitHub discovery, profile evidence, teammate reviews, possible-no-show reporting, and an in-app inbox.

## Quick start — Windows

1. Install Python 3.11 or newer.
2. Extract the project ZIP and open PowerShell in the `CampusConnect` folder.
3. Create a virtual environment and install the requirements:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` if you want to configure keys. The app automatically loads `.env` when `python-dotenv` is installed.
5. Start the server:

   ```powershell
   uvicorn app.main:app --reload
   ```

6. Open <http://127.0.0.1:8000>.

The local SQLite database is created at `data/campus_connect.sqlite3` by default.

## Quick start — macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env  # optional; then edit the keys/settings
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>.

## Sign-up, sign-in, and multiple participants

Every registered person is a separate database account with a unique email, password hash, and server-side session. Registration is not restricted to the seeded demo users. Each invitee must log in with their own account, open the **Inbox**, and explicitly accept or decline their own invitation. That response updates the shared team's persisted state and appears to the captain after refresh.

For local demonstration, one browser session can log out and sign in as another user. For concurrent testing, use separate browsers/incognito windows or different devices so each keeps its own session cookie.

### Seeded demo accounts (local demo mode only)

The default local environment seeds synthetic example data. Password for the demo accounts is `Campus123!`.

| Role | Email |
|---|---|
| Captain | `captain@campusconnect.demo` |
| Backend candidate | `arjun@campusconnect.demo` |
| Frontend candidate | `maya@campusconnect.demo` |
| AI/ML candidate | `karthik@campusconnect.demo` |
| Research / pitching candidate | `rhea@campusconnect.demo` |
| Additional AI/ML candidate | `leena@campusconnect.demo` |
| Backend / DevOps candidate | `sam@campusconnect.demo` |
| Beginner profile | `newbie@campusconnect.demo` |
| Demo administrator | `admin@campusconnect.demo` |

All seeded people and sample events are synthetic. In production, demo accounts and their quick-login buttons are disabled by `CAMPUS_DEMO_MODE=false` and `CAMPUS_SEED_DEMO=false`.

## Add custom people

In the app, sign in to an admin account and open **Admin & trust**. Use **Add and customise people** to create a candidate account with a unique email, initial password, skills, availability, interests, links, projects, and hackathon history. The account can sign in independently and change its profile. Share credentials privately; do not place real passwords in issue trackers or source control.

## Team Architect and bulk invitations

The captain opens a team workspace and clicks **Run Team Architect**. The workflow retrieves event requirements and eligible profiles, calculates the overall profile evidence and team-specific compatibility score, and presents a ranked candidate list plus a suggested group. **Invite all suggested** creates an individual pending invitation and inbox message for each selected candidate, within the team's available primary slots (or the single backup slot). It does not add them to the roster automatically. Each recipient must accept or decline. Rejections trigger a recovery shortlist, and the captain can run Team Architect again.

The **Team Compatibility Score is only returned for the captain's team-matching workflow**. It is not displayed on the candidate's own profile or join-application screen. A candidate's **Overall Profile Score** is a separate, evidence-informed prototype heuristic.

## Automatic GitHub profile evidence

In **My profile**, enter a GitHub username or a standard `https://github.com/<username>` profile URL and save. Campus Connect attempts a best-effort refresh of public GitHub profile/repository metadata automatically when the username is new or the cached snapshot is at least 30 days old. There is no manual “Sync public GitHub evidence” button. If GitHub is unreachable or rate-limited, saving the rest of the profile still works. Set `GITHUB_TOKEN` to increase the public API rate limit.

The score can include public GitHub repository metadata, self-reported profile details, linked project entries, listed hackathon history, and eligible post-collaboration reviews. A GitHub URL is not proof of authorship or skill, and a LinkedIn URL is not scraped. Scores are transparent, configurable prototype heuristics, not validated predictions. Missing evidence is treated as unknown instead of zero skill.

## Sarvam AI connection

The app supports **Sarvam AI** for an optional LLM-powered, read-only matching review. It still runs the deterministic matching, hard constraints, invitation state changes, backup enforcement, and recovery flow if no LLM key is set.

1. Create a Sarvam API key in the Sarvam dashboard.
2. Put it in `.env` locally (never in browser JavaScript or source control):

   ```dotenv
   AI_PROVIDER=sarvam
   SARVAM_API_KEY=your_real_key_here
   SARVAM_MODEL=sarvam-105b
   SARVAM_BASE_URL=https://api.sarvam.ai/v2
   ```

3. Restart the server and visit `/api/health`. It should report `llm_provider: sarvam` and `llm_configured: true`.

Campus Connect calls Sarvam's OpenAI-compatible Chat Completions V2 endpoint using the `api-subscription-key` header. The model reviews an already-generated candidate shortlist through read-only tools; it cannot change scores, send invitations, bypass constraints, or mutate membership. See the [Sarvam Chat Completion docs](https://docs.sarvam.ai/api/api-guides-tutorials/chat-completion/overview) and [authentication docs](https://docs.sarvam.ai/api-reference/authentication).

If your API key does not have access to the configured model or the provider is temporarily unavailable, the backend records the issue and retains deterministic results. You can change `SARVAM_MODEL` to another model available to your key, after verifying its tool-calling support.

## Deployment on Render

A Render Blueprint is included in `render.yaml`.

1. Push the entire project folder to a private or public GitHub repository (do not commit `.env`).
2. In Render, choose **New → Blueprint** and select that repository.
3. Review `render.yaml`. The blueprint provisions a Python web service, a persistent disk for the SQLite database, production cookie settings, demo mode off, and environment-variable prompts for `SARVAM_API_KEY`, `CAMPUS_ADMIN_EMAIL`, `CAMPUS_ADMIN_PASSWORD`, and `GITHUB_TOKEN`.
4. Enter a real Sarvam API key, an administrator email, and an administrator password of at least 12 characters. Use unique secrets. Optional GitHub and SMTP values can be added in the Render Environment settings.
5. Deploy and wait for the health check at `/api/health` to pass.
6. Open the generated Render URL and create user accounts through the registration screen. Log into the administrator with the email/password configured during deployment.

Do not use demo credentials in the public deployment. Do not paste API keys into frontend files or commit them to GitHub.

### Persistence and scaling note

This Render config uses a **single web instance and persistent SQLite disk**, suitable for a hackathon/pilot deployment. Multiple people can use the same deployed URL concurrently and share accounts, teams, and inbox state. Do not increase the instance count or run multiple app replicas with SQLite. For horizontal scaling, migrate persistence to a managed PostgreSQL database and update the database adapter first.

The application is prepared for deployment, but I cannot deploy it into your own Render/Sarvam accounts without your repository/account actions and secret values. Those are intentionally not embedded in the project.

## Optional email delivery

The in-app inbox always persists messages. To enable email for users who choose “Campus Connect inbox + email”, configure `CAMPUS_SMTP_HOST`, `CAMPUS_SMTP_PORT`, `CAMPUS_SMTP_USERNAME`, `CAMPUS_SMTP_PASSWORD`, and `CAMPUS_SMTP_FROM` in the host's environment settings. Without a real SMTP configuration, external delivery is unavailable and the UI should not claim an email was sent.

## Other integrations and limitations

- **Events:** The app discovers events from its database. Seeded cards are labeled synthetic demos. There is no full multi-site event aggregator configured yet; add sourced events via the admin page.
- **GitHub research:** Public repository search uses GitHub's API where reachable, with a labelled curated fallback. A token can raise rate limits.
- **LinkedIn:** A profile URL can be saved; LinkedIn is not scraped. A link alone does not contribute evidence points.
- **Possible Deserter:** This is an event-specific, multi-reporter warning, not an automatic finding of misconduct. The reported user can respond, and an admin can uphold or dismiss a case. Pending allegations do not affect the Overall Profile Score.
- **Production hardening:** Before a broad public launch, add verified email/account recovery delivery, rate limiting, a fuller account deletion/data-retention policy, deeper security review, and managed PostgreSQL if horizontal scaling is needed.

## Tests

Run the backend integration tests:

```bash
pytest -q
```

Use a separate test database so testing doesn't alter your local demo:

**PowerShell**
```powershell
$env:CAMPUS_DB_PATH = "$env:TEMP\campus-connect-test.sqlite3"
pytest -q
```

**macOS / Linux**
```bash
CAMPUS_DB_PATH=/tmp/campus-connect-test.sqlite3 pytest -q
```

## Helpful endpoints

- App: `/`
- API docs: `/docs`
- Health: `/api/health`
- Demo configuration: `/api/demo-accounts`

## Project structure

```text
CampusConnect/
├── app/
│   ├── main.py        # FastAPI app, account APIs, team flows, integrations
│   ├── db.py          # SQLite schema, migrations, demo seed, admin bootstrap
│   ├── scoring.py     # Overall profile and captain-only compatibility scores
│   ├── agents.py      # Orchestrated tool-using Sarvam/OpenAI-compatible review
│   └── static/
│       ├── index.html
│       ├── styles.css
│       └── app.js
├── data/              # Local SQLite database (runtime-created)
├── tests/
├── render.yaml        # Render Blueprint deployment
├── Procfile
├── runtime.txt
├── requirements.txt
└── .env.example
```
