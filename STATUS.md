# Morning Brief - Project Status

**Last updated:** 2026-02-18

## What It Does

Morning Brief is an automated content summarization pipeline that monitors YouTube channels, fetches new videos, generates AI-powered summaries, and presents them in a static viewer with text-to-speech support.

## Requirements

| Requirement | Status |
|---|---|
| Monitor 10-20 YouTube channels | 4 channels active (expanding) |
| Monitor 10 Spotify podcasts | Phase 2 (not started) |
| Monitor 10 web pages | Phase 3 (not started) |
| Daily automated runs via GitHub Actions | Built, not yet deployed |
| Process only new content (state tracking) | Done |
| Sources mapped to categories via YAML config | Done |
| Adaptive summaries (Hook / Key Findings / So What) | Done |
| Daily digest with links to source and summaries | Done |
| Viewable on computer/phone (GitHub Pages) | Built, not yet deployed |
| Listenable via TTS at 1.0x, 1.2x, 1.5x, 2.0x | Done |
| Content expires after 7 days | Done |
| Error reports generated as .md files | Done |
| Budget $0-$20/mo | On track ($0 currently) |
| Unit tests for all critical logic | **219 tests passing** |

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
| Hosting | GitHub Pages | Free |
| Config | YAML | - |
| State | JSON file | - |

## Current Config

- **Channels (4 active):**
  - Dr. Stacy Sims (Health)
  - AI Explained (AI)
  - Nate B Jones (AI)
  - CangrejoPistolero (Humanities, Spanish)
- **Commented out:** Sam Witteveen (AI), Cold Fusion (AI)
- **Model:** gemini-2.5-flash
- **Lookback:** 168 hours (7 days) — set to 26h for production
- **Max videos/channel:** 1 — increase to 3 for production
- **Active categories:** AI, Health, Humanities
- **Commented out:** Photography, Travel, Politics

## Latest Run (2026-02-18)

4 videos summarized successfully:

| Title | Channel | Category | Duration |
|---|---|---|---|
| The Two Best AI Models/Enemies Just Got Released Simultaneously | AI Explained | AI | 19m 50s |
| The 5 Levels of AI Coding (Why Most of You Won't Make It Past Level 2) | Nate B Jones | AI | 42m 15s |
| Is Yoga Enough for Women? What Women Need for Strength & Longevity | Dr. Stacy Sims | Health | 3m 52s |
| The BIGGEST SCAM in MODERN ART | CangrejoPistolero | Humanities | 22m 16s |

State tracks 4 processed video IDs.

## Summary Format

Adaptive length based on video duration using 3-layer architecture:
- **Short videos (<5 min):** 100-150 words
- **Medium videos (10-20 min):** 300-500 words
- **Long videos/podcasts (60+ min):** 600-800 words

Structure:
1. **The Hook** - 1-2 sentences on why this matters now
2. **Key Findings** - 3-5 bullet points with data/specifics
3. **The So What?** - Broader context and implications

Golden rule: summary never takes more than 10% of video length to read.

## API Usage (per run)

- **YouTube requests:** ~3-5 per channel (1 yt-dlp listing + 1-3 transcript fetches)
- **Gemini API calls:** 1 per video
- **Free tier limits:** 20 requests/day, 5 RPM for Gemini 2.5 Flash
- **Throttle:** 5s between Gemini calls + exponential backoff on 429

## File Inventory

### Source Code (src/)
| File | Purpose | Tests |
|---|---|---|
| config.py | YAML config loader with dataclasses + validation | 18 tests |
| fetchers/youtube.py | Video listing (yt-dlp) + transcripts (youtube-transcript-api) | 17 tests |
| summarizer.py | Gemini API with adaptive prompt, retry, throttle | 15 tests |
| generator.py | Markdown generation (summaries, digest, errors) | 22 tests |
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
| .gitignore | Excludes .venv, __pycache__, etc. |
| .github/workflows/morning-brief.yaml | GitHub Actions workflow |
| mockups/option-a-editorial.html | UI mockup — editorial style |
| mockups/option-b-cards.html | UI mockup — card style |

## Known Issues

1. **YouTube IP blocking** - Too many rapid requests triggers temporary IP ban. Mitigated by using phone hotspot or waiting. GitHub Actions IPs may also be blocked — may need proxy for production.
2. **Gemini free tier quota** - 20 RPD, 5 RPM on gemini-2.5-flash. Old key (gemini-2.0-flash) shows limit:0. Use gemini-2.5-flash with the Default Gemini Project key.
3. **Viewer requires HTTP server** - Browser blocks fetch() from file:// URLs. Must serve via `python -m http.server` locally or GitHub Pages in production.
4. **Duration formatting** - Shows "3.0m 38.0s" instead of "3m 38s" (float formatting from yt-dlp).

## What's Done (Phase 1 MVP)

- [x] Config system (YAML with validation)
- [x] YouTube video fetching (yt-dlp)
- [x] YouTube transcript fetching (youtube-transcript-api)
- [x] Gemini summarization with adaptive prompt
- [x] Retry with exponential backoff for rate limits
- [x] Markdown generation (individual + daily digest)
- [x] State management (skip already-processed videos)
- [x] Content expiration/cleanup (7-day TTL)
- [x] Static viewer with dark mode, mobile, TTS
- [x] GitHub Actions workflow
- [x] **219 unit tests passing**
- [x] End-to-end local run successful (4 videos summarized today)
- [x] Multi-language support (Spanish — CangrejoPistolero)
- [x] Expanded to 4 channels across 3 categories

## Next Steps

### Immediate (Deploy Phase 1)
1. **Set production config** - Change lookback_hours to 26, max_videos_per_channel to 3
2. **Add more YouTube channels** - Expand to 10-20 channels across all categories
3. **Create GitHub repo** - Push code, add GEMINI_API_KEY secret
4. **Enable GitHub Pages** - Serve output/ as static site
5. **Test GitHub Actions** - Manual trigger, verify end-to-end
6. **Fix duration formatting** - Clean up "3.0m 38.0s" to "3m 38s"

### Short-term Improvements
7. **Optimize API calls** - Review YouTube + Gemini call patterns for reduction opportunities
8. **YouTube IP resilience** - Add proxy support or cookie auth for GitHub Actions runners
9. **Viewer UX** - Auto-expand summaries, improve mobile layout, add search
10. **Error handling** - Better error messages in viewer, retry failed videos on next run

### Phase 2 - Spotify Podcasts
11. Add Spotify podcast fetcher (RSS feeds or Spotify API)
12. Adapt transcript extraction (podcast transcription service or Spotify's built-in)
13. Add podcast-specific summary template (longer format, timeline of topics)

### Phase 3 - Web Pages
14. Add web page fetcher (requests + BeautifulSoup or similar)
15. Extract article text, strip boilerplate
16. Summarize web articles with appropriate prompt

### Future Ideas
- Email digest (daily summary sent to inbox)
- Filtering/favorites in viewer
- Summary quality scoring
- Multi-language support (extend beyond Spanish)
- Mobile app (PWA)

## How to Run

### Local (manual)
```bash
cd /Users/nataly/Documents/VibeCoding/MorningBrief/Summarizer

# Activate venv
source .venv/bin/activate

# Run pipeline
unset GOOGLE_API_KEY && GEMINI_API_KEY=<your-key> python -m src.main --verbose

# Dry run (fetch only, no API calls)
python -m src.main --dry-run --verbose

# View output
python -m http.server 8000 -d output
# Open http://localhost:8000

# Run tests
python -m pytest tests/ -v
```

### Automated (GitHub Actions)
Runs daily at 4am UTC. Can also be triggered manually from the Actions tab.
Requires `GEMINI_API_KEY` set as a repository secret.
