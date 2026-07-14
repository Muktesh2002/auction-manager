# 🔨 Tournament Auction Manager

A sport-agnostic Streamlit app to run and track player auctions — built for a
badminton tournament, reusable for cricket, football, or any team-based sport.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## How the money works

- The auction runs **category-wise** (e.g. `U19`, `Open Men`). Each category
  defines how many players every team picks in it and a **max cap** a team may
  spend inside it (e.g. U19: 2 players/team, cap ₹40L).
- **Leftover cap money does not carry over** to other categories.
- Every purchase also draws from the team's **total purse**, which is tracked
  separately. A team's spendable amount in a category is
  `min(category cap left, total purse left)`.
- **Tiebreaker**: a team may *match* the current highest bid (same amount, via
  the custom-bid panel). The tie goes to the team with the higher **total**
  purse remaining — unspent category caps don't count.

## Workflow

1. **⚙️ Setup** — tournament name, sport, currency, total purse, base price,
   bid increment slabs, and the **category table** (name, players per team,
   max cap).
2. **👥 Teams** — add teams with captains; per-team purse override supported.
3. **🏃 Players** — upload a CSV (`name, category, age, base_price, notes` —
   only `name` required; legacy `role`/`rating` columns are read as
   category/age; see `sample_players.csv`) or add players manually.
4. **🔴 Live Auction** — pick the category on the block, then put a player up
   (pick or random). One-tap bid buttons per team with slab-based increments,
   custom jump bids, match-bid tiebreaker, SOLD/UNSOLD, and undo. Buttons
   auto-disable when a team's category slots are full or a bid would leave it
   unable to fill its remaining slots at base price.
5. **📊 Team Dashboard** — per-team/per-category fill and cap-left table, squad
   cards with total purse bars, spend-by-category chart, auction stats, and
   final-squads CSV export.
6. **📜 Bid Log** — every bid/tie/sold/unsold/undo event, exportable to CSV.

The sidebar shows live pool status plus each team's **total purse left** (the
tiebreaker number) and lets you **save the full auction state to JSON** and
restore it later (Streamlit session state is in-memory, so download a snapshot
periodically during a live auction). Old JSON saves are migrated automatically.

Unsold players can be recycled back into the pool for a second round from the
Players tab.
