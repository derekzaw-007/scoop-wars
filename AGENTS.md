# Scoop Wars starter kit: notes for coding agents

This repo is an agent that finds fresh news stories from vague clues. You are helping a team improve it during a 70-minute hackathon. The agent runs inside `codex exec`; this file is not part of its prompt (play.py runs it in an empty temp directory).

## Layout

- `play.py`: CLI. `run_agent(clue, timeout) -> Card` builds the `codex exec` command (`codex_command()`), passes `prompt.md` plus the clue on stdin, and reads the final card from `--output-last-message`. `ask`, `eval` and `play` all go through `run_agent()`. It may be replaced by any agent (any model, no Codex) that returns a `Card` in time.
- `prompt.md`: the agent's instructions.
- `octen_client.py`: `news_search(body) -> dict`, the only allowed way to search. It routes calls through `$SCOOP_SERVER/octen/news-search` with the team token, which forwards to `https://api.octen.ai/news-search` with the team's `OCTEN_API_KEY` and logs the call. `capture()` collects the queries made in a block, for `Card.searches`.
- `news_search_mcp.py`: MCP server (stdio) exposing the `news_search` tool to Codex, built on `octen_client`.
- `judge.py`: the game's judge for links not in the answer key. `eval` uses it. Don't change it; it won't change how the game server judges.
- `card.schema.json`: the final answer shape, `{"url": str, "summary": str}`, enforced with `--output-schema`.

## Hard constraints

- 45 seconds per clue, including Codex startup. `play.py` kills the run at 41 seconds.
- Every search must go through `octen_client.news_search()` (the game server proxy logs it). Budget: 2 News Search calls per clue; the third returns `{"code": 429}`. Calls are made in parallel at your own risk: both count. A card scores 0 if the team's key made no News Search call during the clue. Do not add other web search tools, browsing, or scraping. Other Octen APIs are allowed.
- The card summary must be one English sentence, whatever language the article is in.
- Keep `card.schema.json` and the `/api/cards` submission format unchanged.

## News Search API

`POST /news-search` with `query` (required, max 500 chars), `count` (1 to 100), `time_range` (`day|week|month|year`) or `start_time`/`end_time` (ISO 8601), `time_basis` (`auto|published|crawled`), `language` (ISO 639-1 list: ar, de, en, es, fr, hi, id, it, ja, ko, nl, pl, pt, ru, th, tr, vi, zh), `include_domains`/`exclude_domains`, `highlight` {enable, max_tokens}, `full_content` {enable, max_tokens}, `subjects` {enable, count 1 to 5, max_sub_news 1 to 20}. The response has `data.results[]` (title, url, highlight, authors, time_published) and `data.subjects[]` (name, summary, time_start, top_news, sub_news[]). Full reference: https://docs.octen.ai/capabilities/news-search

## Scoring

Right on a clue the house agent missed: 4. Right and faster than the house agent: 2, slower: 1. Wrong: -2. Blank: 0. Some clues are fakes (often a real story with one fact changed): blank scores +2, any link -2. So speed matters, every detail of the clue must be checked against the article, and answering blank beats a likely-wrong guess.

## Testing

- `uv run play.py ask "clue"` runs one clue; add `-v` to see Codex's output.
- `uv run play.py eval` runs all practice clues in parallel and scores them like the game (answer key, then judge). `--compare` diffs against the previous run in `runs/`. Use it after every change.
