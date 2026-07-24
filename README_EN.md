# jarvis-write

**A controllable, revisable, consistency-first AI system for long-form novel writing.**

[简体中文](README.md) | English

The hard problem of AI-assisted novel writing isn't producing text — it's keeping a several-hundred-thousand-word story coherent: characters stay in character, foreshadowing gets paid off, and the outline stays editable. jarvis-write is not another "one-click novel generator." Text generation is delegated to the LLM; this project builds the **control layer** around it: a temporal story bible for facts, a foreshadowing scheduler for setups and payoffs, a cascading outline engine for edits, and a tag-based tendency system for style — so a long novel stays controllable, revisable, and traceable from the first chapter to the last.

<!-- 📸 Screenshots / demo GIF TODO: suggested order — 1) writing workbench overview, 2) cascading-edit downstream-impact selection, 3) story bible / foreshadowing board. Drop images into docs/assets/ and embed here with <img src="docs/assets/xxx.png" width="820">. -->

> 💬 **Want to try it without self-hosting?** Scan the QR code to join the QQ group and grab an **invite code** → [see Community below](#community)

## ✨ Three things nobody else has

Most AI writing tools stop at "generation." The value of jarvis-write is what comes after: the story stays **editable, coherent, and yours to steer**:

- **🔗 Cascading outline updates** — edit any chapter of the outline at any time; the system grades the change (minor edits short-circuit at zero LLM cost), analyzes downstream impact, and regenerates affected chapters after you confirm. Existing prose is flagged as stale, and every outline version is kept for rollback. *No comparable open-source project does this.*
- **🧭 Long-range consistency engine** — a temporal story bible (every fact is bound to the chapter range where it holds, so you can query "what state is the character in as of chapter N") plus a four-state foreshadowing scheduler (planted / reinforced / resolved / abandoned, with due-date reminders), with automatic post-chapter extraction of entities and facts back into the bible. Hundreds of thousands of words without contradicting itself.
- **🎚️ Tag-based tendency system** — style, pacing, and tone are no longer hardcoded in prompts: chips + free-form input + savable presets, applied across outline, prose, and polishing. You stay in control end to end.

## Key Features

- **Six-step generation pipeline**: seed → character dynamics → worldbuilding → plot architecture → chapter blueprint → chapter prose (built on a mature Snowflake-Method-style prompt system; see Acknowledgments)
- **Long-range consistency engine**: a temporal story bible (every fact is bound to the chapter range where it holds, so you can query "character state as of chapter N"), a four-state foreshadowing scheduler (planted / reinforced / resolved / abandoned, with due-date reminders), and automatic post-chapter extraction of entities and facts back into the bible
- **Chapter-by-chapter generation with consistency checks**: finalized chapters are automatically diffed against the story bible; conflicts are reported to the user for a decision, never silently rewritten; built-in repeated-phrase detection
- **Cascading outline updates**: edit any chapter of the outline at any time — the system grades the change (minor edits short-circuit with zero LLM cost), analyzes downstream impact, and regenerates affected chapters after user confirmation; existing prose is flagged as stale, and every outline version is kept for rollback
- **Polish engine with locked facts**: full-chapter or selected-passage stylistic polishing while plot facts stay frozen (facts extracted before polishing, verified after); a three-layer "de-AI-flavor" mechanism (standing rules + tendency tag + quantitative before/after scoring)
- **Tag-based tendency system**: chips + free-form input + savable presets, applied across outline, prose, and polishing — style, pacing, and tone are the user's choice, not hardcoded prompts
- **Full-book reader**: adjustable themes (paper / kraft / night), fonts, and font sizes
- **Multi-user**: JWT auth + invite-code registration + per-user LLM API keys + data isolation; mobile-friendly UI
- **Export & usage stats**: whole-book export to txt / epub; unified token usage metering with live totals
- **One-command Docker deployment**: single container, frontend served by FastAPI, data persisted in a named volume

## Quick Start

### Option 1: Docker (recommended)

```bash
git clone https://github.com/ynnyh/jarvis-write.git
cd jarvis-write

# Set the required environment variables (see "Configuration" below), then:
docker compose up --build
```

Open `http://localhost:8000` (override the host port with the `PORT` variable). SQLite data is persisted in the named volume `jarvis_write_data`.

### Option 2: Local development

```bash
# Backend (first time: create a venv, pip install -r requirements.txt,
# cp .env.example .env and configure a key)
cd backend && python -m app        # http://127.0.0.1:8000

# Frontend (separate terminal, /api proxied to 8000)
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Full setup, smoke tests, and directory layout: [backend/README.md](backend/README.md) (Chinese).

## Configuration

| Setting | Description |
|---|---|
| `JWT_SECRET` | JWT signing key, **required** — must be a long random string (otherwise tokens can be forged on a public deployment). With `APP_ENV=prod`, startup is **refused** if the weak default is still in use |
| `ADMIN_PASSWORD` | Initial admin password, **required** (no default under Docker; the in-code default is for local development only) |
| `INVITE_CODE` | Invite code for registration; **leave empty to disable registration entirely** |
| LLM API keys | DeepSeek / OpenAI / Gemini supported. Each account configures its own key on the **settings page** (stored in the database, recommended); `.env` values act as a fallback |

Full list of options: [backend/.env.example](backend/.env.example).

## Documentation

The design docs are written in Chinese:

| Document | Contents |
|---|---|
| [docs/00-overview.md](docs/00-overview.md) | Vision, design rationale, and how it compares to similar projects |
| [docs/01-architecture.md](docs/01-architecture.md) | System architecture, code layout, technology choices |
| [docs/02-data-model.md](docs/02-data-model.md) | Data model: all tables, fields, and relations |
| [docs/03-engines.md](docs/03-engines.md) | The three core engines: consistency / outline cascade / polish |
| [docs/04-tag-system.md](docs/04-tag-system.md) | Tag-based tendency system: chips + custom input + presets |
| [docs/05-roadmap.md](docs/05-roadmap.md) | Phased roadmap, acceptance criteria, and implementation deviations |
| [backend/README.md](backend/README.md) | Backend setup, testing, and directory details |

## Tech Stack

- **Backend**: Python 3.12 + FastAPI (REST + SSE), SQLAlchemy 2.x + SQLite (Postgres-ready), Pydantic v2
- **LLM layer**: self-built adapter layer (DeepSeek / OpenAI / Gemini, no LangChain), task-level model routing (strong vs. fast tiers)
- **Frontend**: React + TypeScript + Vite
- **Deployment**: single-container Docker (multi-stage build; frontend assets served by FastAPI at `/app`)

## Status & Roadmap

Phases 0–8 are complete: the generation pipeline and tendency assembler, chapter generation, the long-range consistency engine, the outline cascade engine, the polish engine, the web workbench, token stats and txt/epub export, Docker deployment, and multi-user support with mobile adaptation. Per-phase acceptance results and implementation deviations are recorded in [docs/05-roadmap.md](docs/05-roadmap.md).

Known remaining items:

- **True token-level SSE streaming**: replaced by "async job + five-stage progress polling," which delivers a comparable experience
- **Finer model routing** (separate providers for quality/fast tiers): to be exposed on the settings page when a second provider is integrated

## Testing

```bash
# Backend: API-level + full-pipeline tests with a mocked LLM (isolated temp database)
cd backend && python -m pytest

# Frontend: lint + build
cd frontend && npm run lint && npm run build
```

There are also per-phase self-check scripts (`backend/scripts/stage*_test.py`) — see [backend/README.md](backend/README.md).

<a id="community"></a>

## 🫂 Community

Questions, an **invite code to try the hosted instance**, feature requests, or just want to tinker together — join the QQ group:

<p align="center">
  <img src="docs/assets/qq-group-qr.jpg" alt="jarvis-write QQ group 1006352530" width="240">
</p>

<p align="center"><b>QQ group: 1006352530</b> · scan to join and <b>get a free trial invite code</b></p>

## 🙏 Acknowledgments

This project stands on the shoulders of several excellent open-source projects — the following capabilities draw on their ideas, with thanks (a full, source-read comparison lives in [docs/00-overview.md](docs/00-overview.md), in Chinese):

- **Snowflake-Method prompt system** ← [AI_NovelGenerator](https://github.com/YILING0013/AI_NovelGenerator)
- **Four-state foreshadowing tracking** ← [NovelClaw](https://github.com/iLearn-Lab/NovelClaw)
- **Temporal truth store (facts bound to chapter ranges)** ← [knowrite](https://github.com/knoai/knowrite)
- **Reader-known vs. character-known separation · reveal scheduling · repeated-phrase detection** ← [KazKozDev/NovelGenerator](https://github.com/KazKozDev/NovelGenerator)
- **Knowledge-graph-style story bible** ← [graphify-novel](https://github.com/Anshler/graphify-novel)
- **End-to-end web engineering & layering** ← [AI-Novel-Writing-Assistant](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)

The **cascading outline update engine**, the **tag-based tendency system**, and the work of integrating these "pieces" into one coherent control layer are original to this project.

## License

This project is open-sourced under the [Apache License 2.0](LICENSE). Copyright 2026 ynnyh.
