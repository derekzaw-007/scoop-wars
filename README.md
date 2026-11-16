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

# 3. Check, join, eval
uv run play.py doctor
uv run play.py join "Your Team Name"
uv run play.py eval
```

No uv? `pip install mcp httpx`, then use `python play.py` instead of `uv run play.py`.

## Commands

| Command | What it does |
|---|---|
| `uv run play.py doctor` | Checks Codex, your keys, the game server and a live News Search call |
| `uv run play.py join "Team"` | Registers your team. One laptop per team plays. |
| `uv run play.py ask "clue"` | Runs the agent on any clue you write. Add `-v` to watch Codex work. |
| `uv run play.py eval` | Runs every practice clue, 4 at a time, and scores them the way the game does |
| `uv run play.py eval --compare` | The same, plus what changed since your last eval |
| `uv run play.py play` | Joins the Scoop clock. Start it by 8:25 PM, then hands off the keyboard. |

## Where the points are

The house agent finds most plain clues in about 15 seconds. You won't beat it by finding the same stories the same way.

| You | Points |
|---|---|
| Right on a clue the house agent missed | 4 |
| Right, and your card came in before the house agent's | 2 |
| Right, but after the house agent | 1 |
| Wrong | minus 2 |
| No answer | 0 |

So there are three ways to score: find what it misses, be faster than it on what it finds, and leave a clue blank rather than guess. Every right answer also has a shot at the first-report bonus (below).

## The eval loop

```bash
uv run play.py eval            # about a minute for 15 clues
# change one thing
uv run play.py eval --compare  # did it help?
```

```
clue   result    pts   time srch  house   clue text
p3     right      +2   9.8s    1  14s     A bottle of whisky from a run of only 200 ...
p7     blank      +0  41.0s    8  missed  A short-range drone that a city-state's ...
p9     wrong      -2  17.1s    2  missed  Officers found an anaesthetic being ...
       The article describes the same raid, but it was published outside the 24-hour window.

Right 9/15   Estimated game points +14   Median 16s, slowest 41s
Clues the house agent missed: you got 1/6 (4 points each)
```

- **house** is the house agent's time on that clue, or `missed`. The `missed` rows are worth 4 points each, so they're where to focus.
- **result** comes from the answer key, then the judge: the same model check the game uses for links outside the key. It reads the article and decides whether it reports the same event, was published in the last 24 hours and matches your summary. Wrong and unsure rows print the judge's reason.
- Runs are saved in `runs/`. Times run a little slower 4 at a time; use `--parallel 1` for exact timings.
- More practice clues may unlock during the build. Run eval again when the host says so.

## What to change

| What | Where | Why |
|---|---|---|
| The search tool | `news_search_mcp.py` | Usually the biggest win. Each tool round trip costs Codex several seconds, so one smart call beats four simple ones: run several queries in parallel inside one call, set `language`, use `start_time` for "this morning", trim what you return. |
| Codex settings | `play.py` → `codex_command()`, or `CODEX_MODEL` / `CODEX_REASONING` in `.env` | Speed. Codex startup and reasoning take most of the 45 seconds. |
| The prompt | `prompt.md` | Judgment: when to leave a clue blank, checking dates (last month's version of a story doesn't count), reading story summaries before picking a link. |
| The whole agent | `play.py` → `run_agent()` | You can replace Codex entirely (below). |

Octen News Search reference: https://docs.octen.ai/capabilities/news-search. Other Octen APIs are allowed too.

### Replacing Codex

`run_agent(clue, timeout)` just has to return a `Card` within the timeout. Any model or framework is fine, as long as every search goes through `octen_client.news_search()`. That's what routes your calls through the game server so they count:

```python
import octen_client

def run_agent(clue: str, timeout: float = 41, verbose: bool = False) -> Card:
    started = time.time()
    with octen_client.capture() as searches:
        data = octen_client.news_search({"query": clue, "count": 10, "time_range": "day"})
        url, summary = pick_answer(clue, data)  # your logic, e.g. one LLM call
    return Card(url=url, summary=summary, seconds=time.time() - started, searches=list(searches))
```

`ask`, `eval` and `play` all call `run_agent()`, so they pick up your version.

## Scoring details

- **First report:** 2 more points if your article is the earliest one in the story's answer key, and the key holds at least 3 articles.
- **No News Search call:** if your key made no News Search call (through the game server) between the clue appearing and your card, the card scores 0, even if it's right.
- **Ties:** more right answers wins, then less total time to your right answers.

## Rules

- Every search goes through Octen News Search with your team's key. Other Octen APIs are fine; other search tools and web browsing are not.
- During the Scoop clock your agent plays alone: 20 clues, one a minute. A team that touches the keyboard gets a warning, then loses its next answer.
- The summary on your card is always one sentence in English.
- If the game server goes down, the host reads the clues aloud and you paste your cards into the backup form. `play.py play` prints every card so you can copy it.
