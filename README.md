# Scoop Wars starter kit

This is the house agent: Codex plus Octen News Search, with a plain prompt. It plays every clue in the Scoop clock too. Your job is to beat it.

Each clue describes a story published in the last few hours, without the names or words in its headline. Your agent has 45 seconds to answer with a **card**: the URL of one article that reports the story, and a one-sentence English summary.

## Setup (5 minutes)

You need Python 3.10+, [uv](https://docs.astral.sh/uv/), Node.js and the Codex CLI.

```bash
# 1. Codex CLI, logged in with your ChatGPT account or an OpenAI API key
npm install -g @openai/codex
codex login                                            # ChatGPT Pro
# or: printenv OPENAI_API_KEY | codex login --with-api-key

# 2. This repo
git clone https://github.com/derekzaw-007/scoop-wars.git && cd scoop-wars
cp .env.example .env        # add OCTEN_API_KEY and SCOOP_SERVER (on the big screen)

# 3. Check, join, practice
uv run play.py doctor
uv run play.py join "Your Team Name"
uv run play.py practice
```

No uv? `pip install mcp httpx`, then use `python play.py` instead of `uv run play.py`.

## Commands

| Command | What it does |
|---|---|
| `uv run play.py doctor` | Checks Codex, your keys, the game server and a live News Search call |
| `uv run play.py join "Team"` | Registers your team. One laptop per team plays. |
| `uv run play.py ask "clue"` | Runs the agent on any clue you write. Add `-v` to watch Codex work. |
| `uv run play.py practice` | Runs the practice clues and tells you which the agent got right |
| `uv run play.py play` | Joins the Scoop clock. Start it by 8:25 PM, then hands off the keyboard. |

## How the agent works

```
play.py ──> codex exec ──> news_search (MCP tool) ──> game server ──> Octen News Search
   ^              │
   └── card ──────┘  {"url": "...", "summary": "..."}
```

| File | What to change |
|---|---|
| `prompt.md` | The instructions Codex gets with every clue. The house version is deliberately plain. |
| `news_search_mcp.py` | The `news_search` tool: which parameters Codex can set, how results are trimmed, what comes back. Subjects (Octen's grouping of articles into stories) are on. |
| `play.py` → `codex_command()` | Model, reasoning effort, timeouts. `CODEX_MODEL` and `CODEX_REASONING` in `.env` also work. |

Ideas: use `start_time` for "this morning", read subject summaries before picking a link, search more than once, pick the earliest article for the first-report bonus. Keep an eye on time: 45 seconds includes Codex starting up.

The News Search API reference: https://docs.octen.ai/capabilities/news-search

## Scoring

| Situation | Points |
|---|---|
| Right, and the house agent is also right | 1 |
| Right, and the house agent missed | 4 |
| Your article is the first report of the story (stories with 3 or more articles) | 2 more |
| Wrong | minus 2 |
| No answer in 45 seconds | 0 |
| Your key made no News Search call during the clue | 0, even if right |

Ties go to the team with more right answers, then the one whose right answers came in fastest.

A link counts as right if it's one of the articles Octen grouped into the story, or if the game master rules it reports the same event, was published in the last 24 hours and your summary matches.

## Rules

- Every search goes through Octen News Search with your team's key. Other Octen APIs are fine; other search tools and web browsing are not.
- During the Scoop clock your agent plays alone: 20 clues, one a minute. A team that touches the keyboard gets a warning, then loses its next answer.
- The summary on your card is always one sentence in English.
- If the game server goes down, the host reads the clues aloud and you paste your cards into the backup form. `play.py play` prints every card so you can copy it.
