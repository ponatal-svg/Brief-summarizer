# Morning Brief - Project Status

**Last updated:** 2026-02-19

## What It Does

Morning Brief is an automated content summarization pipeline that monitors YouTube channels, fetches new videos, generates AI-powered summaries, and presents them in a static viewer with text-to-speech support.

## Requirements

| Requirement | Status |
|---|---|
| Monitor 10-20 YouTube channels | 5 channels active (expanding) |
| Monitor 10 Spotify podcasts | Phase 2 (not started) |
| Monitor 10 web pages | Phase 3 (not started) |
| Daily automated runs via GitHub Actions | ✅ Live |
| Process only new content (state tracking) | ✅ Done |
| Sources mapped to categories via YAML config | ✅ Done |
| Adaptive summaries (Hook / Key Findings / So What) | ✅ Done |
| Daily digest with links to source and summaries | ✅ Done |
| Viewable on computer/phone (GitHub Pages) | ✅ Live at ponatal-svg.github.io/Brief-summarizer |
| Listenable via TTS at 1.0x, 1.2x, 1.5x, 2.0x | ✅ Done |
| Content expires after 7 days | ✅ Done |
| Error reports generated as .md files | ✅ Done |
| Budget $0-$20/mo | ✅ On track ($0 currently) |
| Unit tests for all critical logic | ✅ 225 tests passing |

## Architecture

```
config.yaml          - Channels, categories, settings
    |
    v
src/main.py          - Orchestrator (CLI with --dry-run, --verbose)
    |
    +-- src/config.py        - YAML config loader + validation
    +-- src/state.py         - JSON state (tracks processed video IDs)
    +-- src/fetchers/
    |     youtube.py         - yt-dlp (video listing) + youtube-transcript-api (transcripts)
    +-- src/summarizer.py    - Gemini API with adaptive prompt + retry/throttle
    +-- src/generator.py     - Markdown file generation (summaries, digest, errors)
    +-- src/viewer.py        - Static HTML viewer with TTS, dark mode, mobile
    +-- src/cleanup.py       - Removes content older than max_age_days
    |
    v
output/
    index.html               - Static viewer (served via GitHub Pages)
    categories.json           - Category color map
    digest-index.json         - Available dates
    daily/YYYY-MM-DD.md       - Daily digest
    summaries/YYYY-MM-DD/*.md - Individual summaries
    errors/YYYY-MM-DD-errors.md
```

## Tech Stack

| Component | Technology | Cost |
|---|---|---|
| Language | Python 3.12 | Free |
| YouTube video listing | yt-dlp (--flat-playlist) | Free |
| YouTube transcripts | youtube-transcript-api | Free |
| Summarization | Gemini 2.5 Flash API (free tier) | Free |
| Text-to-Speech | Browser Web Speech API | Free |
| Scheduling | GitHub Actions (cron 4am UTC) | Free |
| Hosting | GitHub Pages (public repo) | Free |
| Config | YAML | - |
| State | JSON file | - |

## Current Config

- **Channels (5 active):**
  - Dr. Stacy Sims (Health)
  - AI Explained (AI)
  - Nate B Jones (AI)
  - Sam Witteveen (AI)
  - Cold Fusion (AI)
  - CangrejoPistolero (Humanities, Spanish)
- **Active categories:** AI, Health, Humanities
- **Inactive categories (no channels yet):** Photography, Travel, Politics
- **Model:** gemini-2.5-flash
- **Lookback:** 168 hours (7 days) — change to 26h for production
- **Max videos/channel:** 1 — change to 3 for production

## Latest Run (2026-02-18)

4 videos summarized successfully:

| Title | Channel | Category |
|---|---|---|
| The Two Best AI Models/Enemies Just Got Released Simultaneously | AI Explained | AI |
| The 5 Levels of AI Coding (Why Most of You Won't Make It Past Level 2) | Nate B Jones | AI |
| Is Yoga Enough for Women? What Women Need for Strength & Longevity | Dr. Stacy Sims | Health |
| The BIGGEST SCAM in MODERN ART | CangrejoPistolero | Humanities |

1 video skipped (Sam Witteveen — no transcript available, will retry).

## Summary Format

Adaptive length based on video duration:
- **Short (<5 min):** 100-150 words
- **Medium (10-20 min):** 300-500 words
- **Long (60+ min):** 600-800 words

Structure: **The Hook** → **Key Findings** → **The So What?**

Golden rule: summary never takes more than 10% of video length to read.

## API Usage (per run)

- **YouTube requests:** ~3-5 per channel
- **Gemini API calls:** 1 per video with transcript
- **Free tier limits:** 20 req/day, 5 RPM
- **Throttle:** 5s between calls + exponential backoff on 429

## Error Handling

| Error | Behaviour | Action needed? |
|---|---|---|
| RPM rate limit (429 burst) | Retry ×4 with 5→10→20→40s backoff | No |
| RPD daily quota exhausted | Save partial progress, abort cleanly, retry tomorrow | No |
| 5xx server error | Retry ×4 | No |
| Invalid/expired API key | sys.exit(1) → GitHub Actions red ❌ + email | **Yes — fix secret** |
| Missing API key | sys.exit(1) → GitHub Actions red ❌ + email | **Yes — add secret** |
| No transcript | Skip video, log warning, continue | No |
| YouTube IP block | Channel skipped, error report written | Maybe — retry next day |
| Config error | sys.exit(1) → GitHub Actions red ❌ + email | **Yes — fix config.yaml** |

**Monitoring:** GitHub Actions sends email on failure (configure at github.com/settings/notifications).
Error details written to `output/errors/YYYY-MM-DD-errors.md`.

## File Inventory

### Source Code (src/)
| File | Purpose | Tests |
|---|---|---|
| config.py | YAML config loader with dataclasses + validation | 18 tests |
| fetchers/youtube.py | Video listing (yt-dlp) + transcripts (youtube-transcript-api) | 17 tests |
| summarizer.py | Gemini API with adaptive prompt, retry, throttle, quota handling | 29 tests |
| generator.py | Markdown generation (summaries, digest, errors) + digest merge | 22 tests |
| cleanup.py | Expire old content + state entries | 17 tests |
| state.py | JSON state management (processed IDs) | 6 tests |
| viewer.py | Static HTML/CSS/JS viewer generation | 7 tests |
| main.py | CLI orchestrator | - |

### Config & Support Files
| File | Purpose |
|---|---|
| config.yaml | Source channels, categories, settings |
| requirements.txt | Python dependencies |
| state.json | Processed video IDs (auto-managed) |
| .gitignore | Excludes .venv, cookies.txt, .DS_Store, etc. |
| .github/workflows/morning-brief.yaml | GitHub Actions workflow (runs 4am UTC daily) |
| mockups/ | UI layout experiments |

## Known Issues

1. **Sam Witteveen transcript** — youtube-transcript-api fails on some videos from this channel on GitHub Actions IPs. Will retry on next run.
2. **YouTube IP blocking** — Too many rapid requests triggers temporary IP ban. GitHub Actions IPs may be blocked — may need cookie auth for production.
3. **Viewer requires HTTP server** — Browser blocks fetch() from file:// URLs. Use `python -m http.server` locally or GitHub Pages in production.
4. **lookback_hours / max_videos** — Still set to dev values (168h, 1 video). Change to 26h / 3 videos for full production.

## What's Done

- [x] Config system (YAML with validation)
- [x] YouTube video fetching (yt-dlp)
- [x] YouTube transcript fetching (youtube-transcript-api)
- [x] Gemini summarization with adaptive prompt
- [x] Robust error handling (RPM retry, RPD abort, auth fail, 5xx retry)
- [x] Skip videos with no transcript
- [x] Markdown generation (individual + daily digest)
- [x] Digest merge (multiple runs per day don't overwrite)
- [x] State management (skip already-processed videos)
- [x] Content expiration/cleanup (7-day TTL)
- [x] Static viewer: dark mode, mobile, TTS, date pills, category filters
- [x] GitHub Actions workflow (daily + manual trigger)
- [x] GitHub Pages deployment (public site)
- [x] Failure email notifications via GitHub settings
- [x] 225 unit tests passing
- [x] Multi-language support (Spanish)
- [x] 5 channels across 3 categories

## Next Steps

### Immediate
1. **Set production config** — Change lookback_hours to 26, max_videos_per_channel to 3
2. **Add more channels** — Expand to 10-20 across all categories
3. **Fix Sam Witteveen transcript** — Test cookie auth for GitHub Actions runners

### Short-term
4. **Viewer UX** — Search, auto-expand, improve mobile
5. **Error handling** — Surface error count on viewer UI

### Phase 2 - Spotify Podcasts
6. Add Spotify podcast fetcher (RSS feeds or Spotify API)
7. Podcast-specific summary template (longer format, topic timeline)

### Phase 3 - Web Pages
8. Web page fetcher (requests + BeautifulSoup)
9. Article text extraction, boilerplate stripping

### Future
- Email digest
- Filtering/favorites in viewer
- Mobile app (PWA)

## How to Run

### Local
```bash
source .venv/bin/activate
unset GOOGLE_API_KEY && GEMINI_API_KEY=<your-key> python -m src.main --verbose
python -m src.main --dry-run --verbose   # no API calls
python -m http.server 8000 -d output     # view at localhost:8000
python -m pytest tests/ -v               # run tests
```

### Automated
Runs daily at 4am UTC via GitHub Actions.
Requires `GEMINI_API_KEY` repository secret.
Site: https://ponatal-svg.github.io/Brief-summarizer/
