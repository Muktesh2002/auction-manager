# 🔨 Tournament Auction Manager

A sport-agnostic Streamlit app to run and track player auctions — built for a
badminton tournament, reusable for cricket, football, or any team-based sport.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Workflow

1. **⚙️ Setup** — tournament name, sport, currency, default purse, base price,
   min/max squad size, and bid increment slabs (e.g. `0:100, 2000:250, 5000:500`).
2. **👥 Teams** — add teams with captains; per-team purse override supported.
3. **🏃 Players** — upload a CSV (`name, role, base_price, rating, notes` — only
   `name` required; see `sample_players.csv`) or add players manually.
4. **🔴 Live Auction** — put a player on the block (pick or random), one-tap bid
   buttons per team with slab-based increments, custom jump bids, SOLD/UNSOLD,
   and undo. Buttons auto-disable when a team's squad is full or a bid would
   leave it unable to fill its minimum squad at base price.
5. **📊 Team Dashboard** — purse spent/remaining, max next bid, full rosters,
   auction stats, and final-squads CSV export.
6. **📜 Bid Log** — every bid/sold/unsold/undo event, exportable to CSV.

The sidebar shows live pool/purse status and lets you **save the full auction
state to JSON** and restore it later (Streamlit session state is in-memory, so
download a snapshot periodically during a live auction).

Unsold players can be recycled back into the pool for a second round from the
Players tab.
