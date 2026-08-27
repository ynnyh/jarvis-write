# jarvis-write

**A controllable, revisable, consistency-first AI system for long-form novel writing.**

[简体中文](README.md) | English

The hard problem of AI-assisted novel writing isn't producing text — it's keeping a several-hundred-thousand-word story coherent: characters stay in character, foreshadowing gets paid off, and the outline stays editable. jarvis-write is not another "one-click novel generator." Text generation is delegated to the LLM; this project builds the **control layer** around it: a temporal story bible for facts, a foreshadowing scheduler for setups and payoffs, a cascading outline engine for edits, and a tag-based tendency system for style — so a long novel stays controllable, revisable, and traceable from the first chapter to the last.

<p align="center">
  <img src="docs/assets/screenshots/01-workbench.png" alt="Writing workbench" width="820">
</p>
<p align="center"><i>The writing workbench: a global workshop sidebar on the left; inside a book you move through three zones — Set up / Write / Whole book. The write zone puts the chapter text front and center, with a submission slip and chapter blueprint at the top, an always-on AI sidebar on the right (ask anything / rework this chapter), and every AI edit accepted piece by piece through a diff review</i></p>

<details>
<summary>📸 More screenshots (desktop / mobile)</summary>

| Home · My novels | App lock |
|---|---|
| <img src="docs/assets/screenshots/02-home.png" width="400"> | <img src="docs/assets/screenshots/03-applock.png" width="400"> |

**✨ Story Workshop** — for when an idea of your own shows up: describe the scene in a paragraph and get a vertical short film built around it:

<p align="center">
  <img src="docs/assets/screenshots/04-story-workshop.png" alt="Story workshop" width="400">
</p>

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

## 🎬 Comic-Drama Workshop: turn your finished novel into a comic drama

Beyond writing and editing — the "Book → Comic Drama" tab turns approved chapters into a **complete production bible** for vertical comic-drama short videos, from art direction to trailer. Take it to Jimeng / Kling / Midjourney / CapCut and build the video by the numbers:

```
① Art direction        ② Asset cards         ③ Episodes          ④ Per-episode line        ⑤ Trailer
  (you pick; AI        (consistency          (hook +             script → shots →          30-60s mashup:
   recommends top 3)    anchors: style /      cliffhanger         3-track prompts →         punch open, cuts
                       character / scene)     per episode)        production pack)          freeze + title card
                       + ref sheets
```

- **Consistency is the whole game**: style, character, and scene anchors live in asset cards and are **injected verbatim into every shot's prompts** (with an engine-level fallback when the LLM drops one) — the same face never changes, the art style never drifts across a hundred shots
- **Character reference sheets lock the face**: text anchors shrink drift but never kill it — one click writes a reference-sheet prompt per character (single subject, front view, upper body, plain background, 3:4, carrying the book's style anchor); generate it once, **upload it back onto the character card**, and every shot that character appears in reminds you to feed it as the reference image. *Text anchor + reference image is the most reliable consistency method available today.*
- **One-click paste, shaped to the image site you actually use**: three ready-made variants per shot — ① sites with **only one prompt box** (GPT-image / DALL·E / Doubao / Tongyi) get the negatives folded into the body text as an exclusion clause, so **one paste is all it takes**; ② sites **with a negative box** (Jimeng / Kling / SD / ComfyUI) get body and negatives as separate buttons; ③ Midjourney / Niji get English + `--ar 9:16` + `--no`. All three come from one backend rule set, so the app, the exported bible, and the CSV (which carries a `paste_oneframe` column) never disagree
- **You pick the art direction**: CN-comic painterly / Japanese anime / 3D animation / ink-wash / cyberpunk / live-action — a hard constraint on every prompt (live-action carries an uncanny-valley warning; animated styles are the default recommendation), or let AI recommend the top 3 for your book with reasons
- **Director knowledge is baked in**: episodes follow short-drama conventions (self-contained conflict + 3-second hook + cliffhanger; a 3,000-word chapter yields ~4-6 episodes); shots carry framing and camera moves; the production pack lists transitions and per-segment music moods — you supply hands, not brains
- **Production pack**: dubbing script (TTS-friendly text, voice, estimated timing, with mismatch warnings against shot lengths) + edit checklist + **SRT subtitles** that import straight into CapCut/PR
- **One-click trailer**: mash up the highest-impact shots and taglines into a 30-60s promo — punch open, escalation cuts, cliffhanger freeze, title card
- **Voice compliance red line**: voice casting describes timbre characteristics and points to **licensed platform voice libraries** — never clone, never name-imitate a real person's voice; confirm commercial licensing before publishing
- **Same "prompts only" philosophy**: no generation models attached, zero new config — image/video models iterate fast and bill per shot, orchestration and know-how are what we do

## 📣⚡🎂 Promo Workshop & Mood-Clips Workshop & Birthday-Wishes Workshop: a production desk beyond novels

Three more workshops now share the same production pipeline (anchor consistency / three-track prompts / chunking / SRT):

**📣 Promo Workshop (cities / scenic areas / brands)** — talk it through first, then generate:

- **Multi-round creative chat** (true streaming typewriter) with an AI promo director: say "I want to start from food" and get pointed questions, concrete proposals, and per-round consensus recaps; when the direction is clear, distill it into a **creative brief** (positioning / audience / segment structure / slogans / fact-check list) — the brief is the contract for everything downstream, lockable and re-distillable
- **Fact red line**: history, numbers and slogans may only come from the material notes you provide; anything uncertain lands in the fact-check list — a wrong dynasty in a city promo is an accident, not a typo
- Full chain: visual style + landmark cards → narration script → storyboard → three-track prompts → **generation chunks** (shot-boundary grouping ≤5/10/15s with whole-chunk video prompts + first-frame hints, shaped for canvas-splicing workflows) → production pack → four-format export

**⚡ Mood-Clips Workshop (15/30s, dual entry)** — three takes per run, pick one:

- **Generic entry**: ten emotion themes (regret / quarrel / love / childhood / longing / loneliness / healing / heroic / farewell / reunion); each run produces **3 clips with genuinely different takes** (hook → build → punchline caption card), every take shipping three-track prompts, chunks and SRT — short-video is an A/B game, don't polish one draft
- **Novel-derived entry** (Book → "Ad Clips" tab): pulls punchlines and money shots from your approved chapters into book-marketing clips, with **quote grounding** — every quote must cite its original sentence from your prose and is verified by substring match; fabrications get flagged on the spot. It reads your book and quotes your words — that's the gap a generic AI chat can't close

**🎂 Birthday-Wishes Workshop (30/60s, honoree-customized)** — fill in a honoree profile, get three takes per run:

- **Structured honoree profile**: the honoree's name (the greeting must call them by it) / relationship / milestone (1st birthday, coming-of-age, big 60th) / 2-5 concrete memory notes (scenes, inside jokes, catchphrases) / who's sending — that's where all the customization comes from. Six tones (prank / tearjerker / warm daily / surprise reveal / hype milestone / adorable) each carry a **three-act rhythm contract** (name-drop or hook in the first 3 seconds → memory montage grounded in concrete objects → climax frozen on the candle-blowing / hug frame)
- **Kid-oriented style packs (six built-in worlds)**: Peppa-style flat doodle animation / Ultraman-style tokusatsu hero / 3D rescue-team / dinosaur world / fairytale castle / little astronaut — each pack bundles a strong art anchor + world scene vocabulary + a **protagonist-insertion directive**: the birthday kid stars inside that world in every single shot (transforms into a little hero fighting beside the giant, captains the dog rescue team…). Copyright posture matches the ghibli precedent (labels hint "same vibe", prompts describe aesthetics only, never IP names); upload the kid's photo as reference for image-to-video and they appear inside that world
- **Memory grounding**: storyboards must land on the memory notes you gave; the engine checks each one by keyword overlap and flags anything it can't verify into a "please confirm" list — the worst thing a custom video can do is invent memories that never happened
- **Per-chunk tooling guide**: the handcard and shoot board label each chunk with the recommended path — plain chunks go text-to-video, **memory-montage chunks take a real photo of the honoree into image-to-video** (prompts already include "preserve the facial features and build from the reference photo"), and if you want the photo itself to speak the greeting, do that line in a lip-sync tool separately. Same "prompts only, no generation models" philosophy — you still render in Jimeng / Kling / CapCut

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
- **Comic-drama workshop (derivative adaptation)**: turns approved chapters into a full storyboard bible for vertical comic-drama short videos — pick an art direction yourself (CN-comic painterly / Japanese anime / 3D animation / ink-wash / cyberpunk / live-action, or let AI recommend the top 3 with reasons) → art style / character / scene cards (style anchor + appearance anchor injected verbatim into every shot for consistency; characters also get **reference-sheet** prompts you can generate and upload, lifting face-locking from text to pixels) → episode planning (opening hook + cliffhanger per episode) → per-episode script (dialogue / narration modes) → shot list → three-track drawing prompts (layered Chinese / English MJ / negative, with **one-click paste shaped to your image site**: single-box full-text variant / dual-box split variant / MJ parameter variant, same rules in the bible and the CSV) → production pack (voice casting, dubbing script, edit checklist, SRT subtitles; TTS via licensed platform voice libraries, no voice cloning) → one-click trailer mashup (high-impact shots + tagline copy, 30-60s); export as Markdown / CSV / JSON / pack / .srt and take it to Jimeng / Kling / Midjourney / CapCut. Pipeline stepper + asset-rail studio layout. Same "prompts only" philosophy — no generation models attached
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
