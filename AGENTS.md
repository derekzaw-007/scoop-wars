# Scoop Wars starter kit: notes for coding agents

This repo is an agent that finds fresh news stories from vague clues. You are helping a team improve it during a 70-minute hackathon. The agent runs inside `codex exec`; this file is not part of its prompt (play.py runs it in an empty temp directory).

## Layout

- `play.py`: CLI. `run_agent()` builds the `codex exec` command (`codex_command()`), passes `prompt.md` plus the clue on stdin, and reads the final card from `--output-last-message`. `practice`, `ask` and `play` all go through `run_agent()`.
- `prompt.md`: the agent's instructions.
- `news_search_mcp.py`: MCP server (stdio) exposing the `news_search` tool. Calls go to `$SCOOP_SERVER/octen/news-search` with the team token, which forwards to `https://api.octen.ai/news-search` with the team's `OCTEN_API_KEY`.
- `card.schema.json`: the final answer shape, `{"url": str, "summary": str}`, enforced with `--output-schema`.

## Hard constraints

- 45 seconds per clue, including Codex startup. `play.py` kills the run at about 41 seconds.
- Every search must go through Octen News Search via the game server (the proxy logs it). A card scores 0 if the team's key made no News Search call during the clue. Do not add other web search tools, browsing, or scraping. Other Octen APIs are allowed.
- The card summary must be one English sentence, whatever language the article is in.
- Keep `card.schema.json` and the `/api/cards` submission format unchanged.

## News Search API

`POST /news-search` with `query` (required, max 500 chars), `count` (1 to 100), `time_range` (`day|week|month|year`) or `start_time`/`end_time` (ISO 8601), `time_basis` (`auto|published|crawled`), `language` (ISO 639-1 list: ar, de, en, es, fr, hi, id, it, ja, ko, nl, pl, pt, ru, th, tr, vi, zh), `include_domains`/`exclude_domains`, `highlight` {enable, max_tokens}, `full_content` {enable, max_tokens}, `subjects` {enable, count 1 to 5, max_sub_news 1 to 20}. The response has `data.results[]` (title, url, highlight, authors, time_published) and `data.subjects[]` (name, summary, time_start, top_news, sub_news[]). Full reference: https://docs.octen.ai/capabilities/news-search

## Testing

- `uv run play.py ask "clue"` runs one clue; add `-v` to see Codex's output.
- `uv run play.py practice` runs the practice clues from the game server and checks the answers.
