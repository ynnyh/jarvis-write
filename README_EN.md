# jarvis-write

**A controllable, revisable, consistency-first AI system for long-form novel writing.**

[简体中文](README.md) | English

The hard problem of AI-assisted novel writing isn't producing text — it's keeping a several-hundred-thousand-word story coherent: characters stay in character, foreshadowing gets paid off, and the outline stays editable. jarvis-write is not another "one-click novel generator." Text generation is delegated to the LLM; this project builds the **control layer** around it: a temporal story bible for facts, a foreshadowing scheduler for setups and payoffs, a cascading outline engine for edits, and a tag-based tendency system for style — so a long novel stays controllable, revisable, and traceable from the first chapter to the last.

<p align="center">
  <img src="docs/assets/screenshots/01-workbench.png" alt="Writing workbench" width="820">
</p>
<p align="center"><i>The writing workbench: six-step pipeline navigation and a chapter map (review status, word counts) on the left; chapter-by-chapter generation / reading / rewriting on the right</i></p>

**🎬 The cascading outline update, end to end in 30 seconds:**

<p align="center">
  <img src="docs/assets/screenshots/demo-cascade.gif" alt="Cascading outline update demo" width="820">
</p>

<details>
<summary>📸 More screenshots (desktop / mobile)</summary>

| Home · My novels | App lock |
|---|---|
| <img src="docs/assets/screenshots/02-home.png" width="400"> | <img src="docs/assets/screenshots/03-applock.png" width="400"> |

The mobile UI is fully adapted — just open it in a phone browser:

<p>
  <img src="docs/assets/screenshots/mobile-01.jpg" width="180">
  <img src="docs/assets/screenshots/mobile-02.jpg" width="180">
  <img src="docs/assets/screenshots/mobile-03.jpg" width="180">
  <img src="docs/assets/screenshots/mobile-04.jpg" width="180">
</p>

</details>

> 🖥️ **Download the desktop app (Windows installer, works out of the box)** → [GitHub Releases](https://github.com/ynnyh/jarvis-write/releases/latest)
>
> 🌐 **Website & online docs** → [ynnyh.github.io/jarvis-write](https://ynnyh.github.io/jarvis-write/)
>
> 💬 **Want to try it without self-hosting?** Scan the QR code to join the QQ group and grab an **invite code** → [see Community below](#community)

## ✨ Four things nobody else has

Most AI writing tools stop at "generation." The value of jarvis-write is what comes after: the story stays **editable, coherent, and yours to steer** — and this release makes the hardest problem of all, *"you can tell an AI wrote this,"* a top priority:

- **🔗 Cascading outline updates** — edit any chapter of the outline at any time; the system grades the change (minor edits short-circuit at zero LLM cost), analyzes downstream impact, and regenerates affected chapters after you confirm. Existing prose is flagged as stale, and every outline version is kept for rollback. *No comparable open-source project does this.*
- **🧭 Long-range consistency engine** — a temporal story bible (every fact is bound to the chapter range where it holds, so you can query "what state is the character in as of chapter N") plus a four-state foreshadowing scheduler (planted / reinforced / resolved / abandoned, with due-date reminders), with automatic post-chapter extraction of entities and facts back into the bible. Hundreds of thousands of words without contradicting itself.
- **🎚️ Tag-based tendency system** — style, pacing, and tone are no longer hardcoded in prompts: chips + free-form input + savable presets, applied across outline, prose, and polishing. You stay in control end to end.
- **🧬 De-AI-flavor · dual anchoring + self-healing** — treats *"reads like an AI wrote it"* as the number-one enemy. A **positive anchor** feeds famous-author / preset style exemplars (Yu Hua, Lu Xun, Wang Zengqi, Jin Yong, Wang Xiaobo, Hemingway… your pick — or feed your own sample, or auto-extract one from chapters you've approved) so the prose has a concrete voice to learn from; a **negative anchor** uses ✗AI-cliché→✓human paired counter-examples to cross out stock phrasing. Every final draft then passes a **quantitative AI-flavor gate** (9 cliché rule classes + sentence-rhythm / paragraph-structure statistics); over the threshold, the text is **rewritten to strip the flavor → re-scored → converged**, and reverted if it didn't actually improve — never shipping a worse version. The positive anchor reaches every entry point: continuation, passage polish, whole-chapter polish. *Famous-author styles are always labeled "style reference, not an excerpt from the original"; exemplars are our own pastiche and contain no copyrighted text.*

## Key Features

- **Six-step generation pipeline**: seed → character dynamics → worldbuilding → plot architecture → chapter blueprint → chapter prose (built on a mature Snowflake-Method-style prompt system; see Acknowledgments). The chapter blueprint now **grows chapter by chapter**, with live "generated N / M chapters" progress instead of waiting on an opaque batch call
- **Chapter-title styles & one-click batch rename**: set the tone for every title before generating the blueprint — **plain / hook / suspense / poetic** — curing the "every AI title screams *Earth-Shattering Reversal!*" problem; for an existing book you can **re-title the whole book in one click** from the outline page, with an old→new preview you can check, edit, and apply — titles only, plot untouched, and existing prose is never flagged as stale
- **Long-range consistency engine**: a temporal story bible (every fact is bound to the chapter range where it holds, so you can query "character state as of chapter N"), a four-state foreshadowing scheduler (planted / reinforced / resolved / abandoned, with due-date reminders), and automatic post-chapter extraction of entities and facts back into the bible
- **Chapter-by-chapter generation with consistency checks**: finalized chapters are automatically diffed against the story bible; conflicts are reported to the user for a decision, never silently rewritten; built-in repeated-phrase detection
- **Cascading outline updates**: edit any chapter of the outline at any time — the system grades the change (minor edits short-circuit with zero LLM cost), analyzes downstream impact, and regenerates affected chapters after user confirmation; existing prose is flagged as stale, and every outline version is kept for rollback
- **Polish engine with locked facts**: full-chapter or selected-passage stylistic polishing while plot facts stay frozen (facts extracted before polishing, verified after)
- **De-AI-flavor (dual anchoring + self-healing)**: a positive anchor — famous-author / preset style exemplars (Yu Hua / Lu Xun / Wang Zengqi and more, or feed your own sample / auto-extract from approved chapters) plus ✗AI-cliché→✓human paired counter-examples; detection — 9 cliché rule classes (including a heavy-penalty path for hollow "ripples in the mind / a voice calling from afar / an invisible hand" metaphors) + 4 sentence/paragraph statistics scoring an AI-flavor index; a gate — an over-threshold final draft is auto-rewritten to strip the flavor, re-scored and converged, reverted if not improved. The positive anchor is wired into continuation / passage polish / whole-chapter polish. Famous-author styles are labeled "style reference, not an excerpt from the original" (pastiche, no original text)
- **Tag-based tendency system**: chips + free-form input + savable presets, applied across outline, prose, and polishing — style, pacing, and tone are the user's choice, not hardcoded prompts
- **Creative preference profile**: style / taboos / target audience / other directives are distilled into a structured, project-level profile that acts as the highest-priority constraint across every generation step; claims made during a discussion can be absorbed with one click, and an existing book auto-extracts and enables a profile from its prose the first time it's opened
- **Word-count guard with auto chapter-splitting**: an over-length final draft is automatically compressed and rewritten; a severely over-length one is auto-split (LLM picks the break point + all numbering shifts + bible/summary rebuilt), with structural changes and prose committed atomically in one transaction so a mid-way crash never corrupts the text
- **Editorial echo**: lead-reviewer / proofreader results are retained — the auto-fix list and the manual to-fix list from generation are visible the moment you open them, and they auto-invalidate as soon as the prose changes, so no stale guidance lingers
- **Discuss before you rewrite**: before a rewrite, multi-turn dialogue with the AI distills precise revision notes, which the rewrite then follows — no more "the AI guessing what you want"
- **Real-time streaming AI chat**: replies in passage discussion, whole-chapter Q&A, and rewrite dialogue now **stream token by token** (like someone typing live) — a spinner until the first token, then the bubble settles into the distilled revision notes / rewrite suggestion; built on fetch + ReadableStream hand-framing (auth-aware) so a relay / CDN proxy can't buffer it into one lump
- **Full-book reader**: adjustable themes (paper / kraft / night), fonts, and font sizes; paragraph-level AI Q&A and polishing — select to ask, accept to replace
- **Comic-drama workshop (derivative adaptation)**: turns approved chapters into a full storyboard bible for vertical comic-drama short videos — pick an art direction yourself (CN-comic painterly / Japanese anime / 3D animation / ink-wash / cyberpunk / live-action, or let AI recommend the top 3 with reasons) → art style / character / scene cards (style anchor + appearance anchor injected verbatim into every shot for consistency) → episode planning (opening hook + cliffhanger per episode) → per-episode script (dialogue / narration modes) → shot list → three-track drawing prompts (layered Chinese / English MJ / negative) → production pack (voice casting, dubbing script, edit checklist, SRT subtitles; TTS via licensed platform voice libraries, no voice cloning) → one-click trailer mashup (high-impact shots + tagline copy, 30-60s); export as Markdown / CSV / JSON / pack / .srt and take it to Jimeng / Kling / Midjourney / CapCut. Pipeline stepper + asset-rail studio layout. Same "prompts only" philosophy — no generation models attached
- **Multi-user**: JWT auth + invite-code registration + per-user LLM API keys + data isolation; mobile-friendly UI
- **Export & usage stats**: whole-book export to txt / epub; unified token usage metering with live totals
- **One-command Docker deployment**: single container, frontend served by FastAPI, data persisted in a named volume
- **Desktop app (Windows)**: an installer that works out of the box — no login, runs fully offline, data stays on your machine; built and published to Releases automatically by GitHub Actions

## Quick Start

### Option 1: Desktop installer (easiest · Windows)

Download the latest `jarvis-write_<version>_x64-setup.exe` from [GitHub Releases](https://github.com/ynnyh/jarvis-write/releases/latest) and double-click to install. It runs offline with no login required, and your work is stored locally (`%APPDATA%\jarvis-write`) — no deployment, no database setup. On first launch, enter your own LLM API key under "Model Settings" and you're ready to write.

### Option 2: Docker (self-hosted multi-user service)

```bash
git clone https://github.com/ynnyh/jarvis-write.git
cd jarvis-write

# Set the required environment variables (see "Configuration" below), then:
docker compose up --build
```

Open `http://localhost:8000` (override the host port with the `PORT` variable). SQLite data is persisted in the named volume `jarvis_write_data`.

### Option 3: Local development

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
| LLM API keys | DeepSeek / OpenAI / Gemini and any OpenAI-compatible relay supported. Each account configures its own keys on the **settings page** — multiple named configs with one-click default (quality tier) / fast-tier switching (stored in the database, recommended); `.env` values act as a fallback |

Full list of options: [backend/.env.example](backend/.env.example).

## Documentation

> 🌐 Online docs site (all design docs + local search): [ynnyh.github.io/jarvis-write](https://ynnyh.github.io/jarvis-write/)

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
- **LLM layer**: self-built adapter layer (DeepSeek / OpenAI / Gemini, no LangChain), task-level model routing (strong vs. fast tiers, each mapped to its own config), cc-switch-style multi-config management, automatic retry with streaming fallback for transient failures (survives CDN timeouts on long generations)
- **Frontend**: React + TypeScript + Vite
- **Deployment**: single-container Docker (multi-stage build; frontend assets served by FastAPI at `/app`)
- **Desktop**: Tauri 2 shell + PyInstaller-frozen backend, with GitHub Actions automatically building the NSIS installer and publishing it to Releases

## Status & Roadmap

Phases 0–8 are complete: the generation pipeline and tendency assembler, chapter generation, the long-range consistency engine, the outline cascade engine, the polish engine, the web workbench, token stats and txt/epub export, Docker deployment, and multi-user support with mobile adaptation. Per-phase acceptance results and implementation deviations are recorded in [docs/05-roadmap.md](docs/05-roadmap.md).

Known remaining items:

- **Token-level streaming**: AI chat (passage / whole-chapter discussion, rewrite dialogue) is now a true SSE token-by-token typewriter, and blueprint generation reports incremental "chapter-by-chapter" progress; only per-chapter prose generation still uses "async job + progress polling", since it chains multiple review steps (de-AI-flavor / consistency / word-count guard) and is better run as a task

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
