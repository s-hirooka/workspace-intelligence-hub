# Workspace Intelligence Hub Portfolio Edition

Workspace Intelligence Hub is a local-first FastAPI application for searching technical material, managing isolated customer workspaces, reviewing system structure, and comparing social and web analytics. This Portfolio Edition keeps the application architecture and automated tests while replacing operational material with synthetic examples.

## Included capabilities

- Retrieval-augmented search with source citations and workspace isolation
- Incremental document indexing for Markdown, PDF, Word, Excel, CSV, source code, and SQLite snapshots
- Pre-index secret and personal-information detection
- Local image metadata, face-label, and semantic-search workflows
- Authentication, roles, audit logs, backups, schedules, and usage limits
- Optional Meta, Instagram, YouTube, GA4, Google Business Profile, LINE, and CSV analytics connectors
- Static source analysis with an offline Roslyn helper

External integrations remain disabled until the operator supplies credentials in the ignored `.env` file. The repository contains no live credentials, customer exports, production databases, or private source documents.

## Quick start

Requirements are Python 3.12 and Docker Desktop.

```powershell
Copy-Item .env.example .env
Copy-Item sources.example.yaml sources.yaml
docker compose up -d db
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`. The first visit creates the local administrator. Use a password of at least 12 characters.

The synthetic examples under `sample-data/` are safe to index. Do not point a public demonstration at production folders or production credentials.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The publication review and exclusions are documented in [SECURITY_PUBLICATION_REPORT.md](SECURITY_PUBLICATION_REPORT.md).

## Security boundaries

- `.env`, `sources.yaml`, service-account files, private keys, databases, backups, uploads, model weights, and runtime logs are ignored.
- API responses never return stored credential values.
- Secret scanning is rule-based and cannot guarantee that every sensitive value will be detected.
- A private repository remains the appropriate location for production configuration, customer material, and operational exports.
