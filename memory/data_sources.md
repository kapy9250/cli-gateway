# Data Sources: Market Data & News

## 1. Market Data
- **Path:** `/workspace/market-data`
- **Structure:**
  - `raw/`: JSON files (prices, derivatives, onchain, macro)
  - `daily/`: Markdown daily snapshots (human-readable summaries)
  - `historical/`: CSV files for time-series analysis
- **Key Files:** `daily/YYYY-MM-DD.md` (Check this first for daily market overview)

## 2. RSS News
- **Path:** `/workspace/projects/rss-news`
- **Structure:**
  - `data/YYYY-MM-DD/`: News articles organized by category (mainstream, onchain, regulatory)
  - `index/YYYY-MM-DD.md`: Daily summary index
- **Key Files:** `index/YYYY-MM-DD.md` (Daily news digest), `data/YYYY-MM-DD/category/HHMM_title.md` (Full articles)

## Usage Strategy
- **Market Status:** Read `market-data/daily/YYYY-MM-DD.md`
- **Latest News:** Read `rss-news/index/YYYY-MM-DD.md` or search `rss-news/data/`
- **Correlation:** Combine price data from market-data with news events from rss-news to analyze market moves.
