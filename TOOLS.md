# TOOLS.md - Local Notes

Skills define *how* tools work. This file is for *your* specifics — the stuff that's unique to your setup.

## What Goes Here

### Environment gotchas (2026-02-02)
- Some exec contexts run `/bin/sh` (dash). `set -o pipefail` fails. Prefer `bash -lc '...'` or bash shebang.
- `python`/`python3` may not be installed; use Node (`node -e` or JS scripts) for log parsing.

### Polymarket CLOB gotchas (2026-02-02)
- `/book` / `getOrderBook()` can return a "ghost book" (bestBid=0.01, bestAsk=0.99) even when frontend shows normal prices.
- Treat this as `SENTINEL_BOOK`/data issue, not necessarily "market restricted".
- For price decisions, prefer `/price` (buy/sell) + `/midpoint` over `/book` top-of-book.
- In `@polymarket/clob-client`, `getPrice(tokenId, side)` expects side to be lowercase `'buy'/'sell'`.

Things like:
- Camera names and locations
- SSH hosts and aliases  
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras
- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH
- home-server → 192.168.1.100, user: admin

### TTS
- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.
