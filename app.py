"""
Tournament Auction Manager
A sport-agnostic Streamlit app to run and track player auctions
(badminton, cricket, football, ... any team-based tournament).

Run with:  streamlit run app.py
"""

import json
import random
from datetime import datetime

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Auction Manager",
    page_icon="🔨",
    layout="wide",
)

# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "tournament_name": "My Tournament",
    "sport": "Badminton",
    "currency": "₹",
    "default_purse": 10000,
    "default_base_price": 500,
    "min_squad_size": 4,
    "max_squad_size": 6,
    # slabs: "from_amount:increment" pairs
    "increment_slabs": "0:100, 2000:250, 5000:500",
}

PLAYER_COLUMNS = ["name", "role", "base_price", "rating", "notes"]


def init_state():
    ss = st.session_state
    ss.setdefault("config", dict(DEFAULT_CONFIG))
    ss.setdefault("teams", [])      # {name, captain, purse}
    ss.setdefault("players", [])    # {id, name, role, base_price, rating, notes,
                                    #  status: available|sold|unsold, sold_to, sold_price}
    ss.setdefault("bid_log", [])    # every bid + sold/unsold event
    ss.setdefault("current_player_id", None)
    ss.setdefault("current_bid", None)      # {amount, team} or None
    ss.setdefault("sale_history", [])       # stack for undo (sold/unsold events)
    ss.setdefault("next_player_id", 1)


def parse_slabs(text):
    """Parse '0:100, 2000:250' -> sorted [(0, 100), (2000, 250)]."""
    slabs = []
    try:
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            frm, inc = part.split(":")
            slabs.append((float(frm), float(inc)))
    except (ValueError, AttributeError):
        return [(0, 100)]
    slabs.sort()
    return slabs or [(0, 100)]


def increment_for(amount):
    slabs = parse_slabs(st.session_state.config["increment_slabs"])
    inc = slabs[0][1]
    for frm, i in slabs:
        if amount >= frm:
            inc = i
    return inc


def fmt_indian(amount):
    """Indian digit grouping: 1234567 -> '12,34,567'."""
    amount = float(amount)
    neg = amount < 0
    amount = abs(amount)
    whole = int(amount)
    frac = round(amount - whole, 2)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    if frac:
        s += f"{frac:.2f}"[1:]
    return ("-" if neg else "") + s


def fmt_money(amount):
    cur = st.session_state.config["currency"]
    return f"{cur}{fmt_indian(amount)}"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
         "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
         "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
         "Eighty", "Ninety"]


def _two_digits(n):
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")).strip()


def amount_in_words(amount):
    """Indian system words: 1234567 -> 'Twelve Lakh Thirty-Four Thousand ...'."""
    amount = float(amount)
    if amount < 0:
        return "Minus " + amount_in_words(-amount)
    n = int(amount)
    paise = int(round((amount - n) * 100))

    def words(n):
        if n == 0:
            return "Zero"
        parts = []
        crore = n // 10**7
        if crore:
            parts.append(words(crore) + " Crore")
        n %= 10**7
        lakh = n // 10**5
        if lakh:
            parts.append(_two_digits(lakh) + " Lakh")
        n %= 10**5
        thousand = n // 1000
        if thousand:
            parts.append(_two_digits(thousand) + " Thousand")
        n %= 1000
        hundred = n // 100
        if hundred:
            parts.append(_ONES[hundred] + " Hundred")
        n %= 100
        if n:
            parts.append(_two_digits(n))
        return " ".join(parts)

    text = words(n)
    if paise:
        text += f" and {_two_digits(paise)} Paise"
    return text


def get_team(name):
    for t in st.session_state.teams:
        if t["name"] == name:
            return t
    return None


def get_player(pid):
    for p in st.session_state.players:
        if p["id"] == pid:
            return p
    return None


def team_players(team_name, players=None):
    players = players if players is not None else st.session_state.players
    return [p for p in players if p["status"] == "sold" and p["sold_to"] == team_name]


def team_spent(team_name):
    return sum(p["sold_price"] for p in team_players(team_name))


def team_purse_left(team_name):
    team = get_team(team_name)
    return team["purse"] - team_spent(team_name)


def team_max_bid(team_name):
    """Max a team can bid while still affording base price for the
    remaining slots needed to reach min squad size."""
    cfg = st.session_state.config
    left = team_purse_left(team_name)
    count = len(team_players(team_name))
    must_still_buy = max(0, cfg["min_squad_size"] - count - 1)  # after this player
    reserve = must_still_buy * cfg["default_base_price"]
    return left - reserve


def team_is_full(team_name):
    cfg = st.session_state.config
    return len(team_players(team_name)) >= cfg["max_squad_size"]


def log_event(event, player, amount=None, team=None):
    st.session_state.bid_log.append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "event": event,          # BID | SOLD | UNSOLD | UNDO
        "player": player["name"] if player else "",
        "team": team or "",
        "amount": amount if amount is not None else "",
    })


# ---------------------------------------------------------------------------
# Auction actions
# ---------------------------------------------------------------------------

def start_player(pid):
    ss = st.session_state
    ss.current_player_id = pid
    ss.current_bid = None


def place_bid(team_name, amount):
    ss = st.session_state
    player = get_player(ss.current_player_id)
    ss.current_bid = {"amount": amount, "team": team_name}
    log_event("BID", player, amount, team_name)


def sell_current():
    ss = st.session_state
    player = get_player(ss.current_player_id)
    bid = ss.current_bid
    player["status"] = "sold"
    player["sold_to"] = bid["team"]
    player["sold_price"] = bid["amount"]
    log_event("SOLD", player, bid["amount"], bid["team"])
    ss.sale_history.append({"type": "sold", "player_id": player["id"]})
    ss.current_player_id = None
    ss.current_bid = None


def mark_unsold():
    ss = st.session_state
    player = get_player(ss.current_player_id)
    player["status"] = "unsold"
    log_event("UNSOLD", player)
    ss.sale_history.append({"type": "unsold", "player_id": player["id"]})
    ss.current_player_id = None
    ss.current_bid = None


def undo_last():
    ss = st.session_state
    if not ss.sale_history:
        return
    last = ss.sale_history.pop()
    player = get_player(last["player_id"])
    log_event("UNDO", player, player.get("sold_price"), player.get("sold_to"))
    player["status"] = "available"
    player["sold_to"] = None
    player["sold_price"] = None
    # put the player back on the block
    start_player(player["id"])


def add_player(name, role, base_price, rating="", notes="", status="available"):
    ss = st.session_state
    ss.players.append({
        "id": ss.next_player_id,
        "name": str(name).strip(),
        "role": str(role).strip(),
        "base_price": float(base_price),
        "rating": str(rating),
        "notes": str(notes),
        "status": status,
        "sold_to": None,
        "sold_price": None,
    })
    ss.next_player_id += 1


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------

def export_state():
    ss = st.session_state
    return json.dumps({
        "config": ss.config,
        "teams": ss.teams,
        "players": ss.players,
        "bid_log": ss.bid_log,
        "sale_history": ss.sale_history,
        "next_player_id": ss.next_player_id,
    }, indent=2)


def import_state(data):
    ss = st.session_state
    ss.config = {**DEFAULT_CONFIG, **data.get("config", {})}
    ss.teams = data.get("teams", [])
    ss.players = data.get("players", [])
    ss.bid_log = data.get("bid_log", [])
    ss.sale_history = data.get("sale_history", [])
    ss.next_player_id = data.get(
        "next_player_id",
        max([p["id"] for p in ss.players], default=0) + 1,
    )
    ss.current_player_id = None
    ss.current_bid = None


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

init_state()
cfg = st.session_state.config

st.title(f"🔨 {cfg['tournament_name']} — Auction")
st.caption(f"{cfg['sport']} · purse {fmt_money(cfg['default_purse'])} per team · "
           f"squad {cfg['min_squad_size']}–{cfg['max_squad_size']} players")

# ----- Sidebar: live stats + save/load ------------------------------------

with st.sidebar:
    st.header("📌 Auction status")
    players = st.session_state.players
    n_avail = sum(1 for p in players if p["status"] == "available")
    n_sold = sum(1 for p in players if p["status"] == "sold")
    n_unsold = sum(1 for p in players if p["status"] == "unsold")
    c1, c2, c3 = st.columns(3)
    c1.metric("Pool", n_avail)
    c2.metric("Sold", n_sold)
    c3.metric("Unsold", n_unsold)

    if st.session_state.teams:
        st.subheader("Purse remaining")
        for t in st.session_state.teams:
            left = team_purse_left(t["name"])
            pct = left / t["purse"] if t["purse"] else 0
            st.progress(max(0.0, min(1.0, pct)),
                        text=f"**{t['name']}** — {fmt_money(left)}")

    st.divider()
    st.subheader("💾 Save / Load")
    st.download_button(
        "Download auction state (JSON)",
        export_state(),
        file_name=f"auction_{datetime.now():%Y%m%d_%H%M}.json",
        mime="application/json",
        width="stretch",
    )
    uploaded_state = st.file_uploader("Restore from JSON", type="json", key="state_upload")
    if uploaded_state is not None and st.button("Restore state", width="stretch"):
        import_state(json.load(uploaded_state))
        st.success("State restored.")
        st.rerun()

# ----- Tabs -----------------------------------------------------------------

tab_auction, tab_dash, tab_log, tab_players, tab_teams, tab_setup = st.tabs(
    ["🔴 Live Auction", "📊 Team Dashboard", "📜 Bid Log",
     "🏃 Players", "👥 Teams", "⚙️ Setup"]
)

# ====================== SETUP ==============================================

with tab_setup:
    st.subheader("Tournament settings")
    st.caption("Sport-agnostic — set these once per tournament and reuse the app anywhere.")
    with st.container(border=True):
        c1, c2 = st.columns(2)
        name = c1.text_input("Tournament name", cfg["tournament_name"])
        sport = c2.text_input("Sport", cfg["sport"])
        c1, c2, c3 = st.columns(3)
        currency = c1.text_input("Currency symbol", cfg["currency"])
        purse = c2.number_input("Default purse per team", min_value=0.0,
                                value=float(cfg["default_purse"]), step=500.0)
        c2.caption(f"{fmt_money(purse)} — **{amount_in_words(purse)}**")
        base = c3.number_input("Default base price", min_value=0.0,
                               value=float(cfg["default_base_price"]), step=50.0)
        c3.caption(f"{fmt_money(base)} — **{amount_in_words(base)}**")
        c1, c2 = st.columns(2)
        min_sq = c1.number_input("Min squad size", min_value=1,
                                 value=int(cfg["min_squad_size"]))
        max_sq = c2.number_input("Max squad size", min_value=1,
                                 value=int(cfg["max_squad_size"]))
        slabs = st.text_input(
            "Bid increment slabs (`from:increment`, comma separated)",
            cfg["increment_slabs"],
            help="e.g. `0:100, 2000:250, 5000:500` → +100 below 2000, +250 from 2000, +500 from 5000",
        )
        if st.button("Save settings", type="primary"):
            cfg.update({
                "tournament_name": name, "sport": sport, "currency": currency,
                "default_purse": purse, "default_base_price": base,
                "min_squad_size": int(min_sq), "max_squad_size": int(max_sq),
                "increment_slabs": slabs,
            })
            st.success("Settings saved.")
            st.rerun()

    st.divider()
    st.subheader("⚠️ Danger zone")
    c1, c2 = st.columns(2)
    if c1.button("Reset auction results (keep teams & players)"):
        for p in st.session_state.players:
            p.update({"status": "available", "sold_to": None, "sold_price": None})
        st.session_state.bid_log = []
        st.session_state.sale_history = []
        st.session_state.current_player_id = None
        st.session_state.current_bid = None
        st.rerun()
    if c2.button("Clear everything", type="secondary"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

# ====================== TEAMS ==============================================

with tab_teams:
    st.subheader("Teams & captains")
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([3, 3, 2, 1])
        t_name = c1.text_input("Team name", key="add_team_name")
        t_captain = c2.text_input("Captain", key="add_team_captain")
        t_purse = c3.number_input("Purse", min_value=0.0,
                                  value=float(cfg["default_purse"]), step=500.0,
                                  key="add_team_purse")
        c3.caption(f"{fmt_money(t_purse)} — **{amount_in_words(t_purse)}**")
        c4.markdown("&nbsp;")
        if c4.button("➕ Add", width="stretch"):
            if not t_name.strip():
                st.error("Team name is required.")
            elif get_team(t_name.strip()):
                st.error("A team with that name already exists.")
            else:
                st.session_state.teams.append(
                    {"name": t_name.strip(), "captain": t_captain.strip(), "purse": t_purse})
                for k in ("add_team_name", "add_team_captain", "add_team_purse"):
                    del st.session_state[k]
                st.rerun()

    if st.session_state.teams:
        for i, t in enumerate(st.session_state.teams):
            roster = team_players(t["name"])
            with st.container(border=True):
                c1, c2, c3, c4, c5 = st.columns([3, 3, 2, 2, 1])
                c1.markdown(f"**{t['name']}**")
                c2.markdown(f"👑 {t['captain'] or '—'}")
                c3.markdown(f"Purse: {fmt_money(t['purse'])}")
                c4.markdown(f"Players: {len(roster)}")
                if c5.button("🗑️", key=f"del_team_{i}",
                             disabled=bool(roster),
                             help="Remove team (only if it has no players)"):
                    st.session_state.teams.pop(i)
                    st.rerun()
    else:
        st.info("No teams yet — add your teams and captains above.")

# ====================== PLAYERS ============================================

with tab_players:
    st.subheader("Player pool")

    up_col, man_col = st.columns(2)
    with up_col:
        st.markdown("**Upload CSV**")
        template = pd.DataFrame(
            [{"name": "Player One", "role": "Singles", "base_price": cfg["default_base_price"],
              "rating": "A", "notes": ""}], columns=PLAYER_COLUMNS)
        st.download_button("Download CSV template",
                           template.to_csv(index=False),
                           file_name="players_template.csv", mime="text/csv")
        up = st.file_uploader("CSV with columns: name, role, base_price, rating, notes "
                              "(only `name` is required)", type="csv")
        if up is not None and st.button("Import players"):
            df = pd.read_csv(up)
            df.columns = [c.strip().lower() for c in df.columns]
            if "name" not in df.columns:
                st.error("CSV must have a `name` column.")
            else:
                existing = {p["name"].lower() for p in st.session_state.players}
                added = skipped = 0
                for _, row in df.iterrows():
                    nm = str(row["name"]).strip()
                    if not nm or nm.lower() in existing:
                        skipped += 1
                        continue
                    bp = row.get("base_price")
                    bp = cfg["default_base_price"] if pd.isna(bp) else float(bp)
                    add_player(
                        nm,
                        "" if pd.isna(row.get("role")) else row.get("role"),
                        bp,
                        "" if pd.isna(row.get("rating")) else row.get("rating"),
                        "" if pd.isna(row.get("notes")) else row.get("notes"),
                    )
                    existing.add(nm.lower())
                    added += 1
                st.success(f"Imported {added} players"
                           + (f" (skipped {skipped} blank/duplicate)" if skipped else ""))
                st.rerun()

    with man_col:
        st.markdown("**Add manually**")
        with st.container(border=True):
            p_name = st.text_input("Name", key="add_player_name")
            c1, c2 = st.columns(2)
            p_role = c1.text_input("Role / category", key="add_player_role",
                                   help="e.g. Singles, Doubles, Batter, Striker …")
            p_base = c2.number_input("Base price", min_value=0.0,
                                     value=float(cfg["default_base_price"]), step=50.0,
                                     key="add_player_base")
            c2.caption(f"{fmt_money(p_base)} — **{amount_in_words(p_base)}**")
            c1, c2 = st.columns(2)
            p_rating = c1.text_input("Rating / grade", key="add_player_rating")
            p_notes = c2.text_input("Notes", key="add_player_notes")
            if st.button("➕ Add player"):
                if not p_name.strip():
                    st.error("Name is required.")
                elif p_name.strip().lower() in {p["name"].lower() for p in st.session_state.players}:
                    st.error("A player with that name already exists.")
                else:
                    add_player(p_name, p_role, p_base, p_rating, p_notes)
                    for k in ("add_player_name", "add_player_role", "add_player_base",
                              "add_player_rating", "add_player_notes"):
                        del st.session_state[k]
                    st.rerun()

    st.divider()
    if st.session_state.players:
        status_filter = st.multiselect(
            "Filter by status", ["available", "sold", "unsold"],
            default=["available", "sold", "unsold"])
        rows = [p for p in st.session_state.players if p["status"] in status_filter]
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df[["id", "name", "role", "base_price", "rating",
                     "status", "sold_to", "sold_price", "notes"]]
            st.dataframe(df, width="stretch", hide_index=True)

        c1, c2 = st.columns(2)
        del_id = c1.selectbox(
            "Remove a player (available only)",
            [None] + [p["id"] for p in st.session_state.players if p["status"] == "available"],
            format_func=lambda i: "—" if i is None else get_player(i)["name"])
        if del_id and c1.button("Remove player"):
            st.session_state.players = [p for p in st.session_state.players if p["id"] != del_id]
            if st.session_state.current_player_id == del_id:
                st.session_state.current_player_id = None
                st.session_state.current_bid = None
            st.rerun()
        n_unsold_now = sum(1 for p in st.session_state.players if p["status"] == "unsold")
        if c2.button(f"♻️ Move {n_unsold_now} unsold players back to pool (round 2)",
                     disabled=n_unsold_now == 0):
            for p in st.session_state.players:
                if p["status"] == "unsold":
                    p["status"] = "available"
            st.rerun()
    else:
        st.info("No players yet — upload a CSV or add them manually above.")

# ====================== LIVE AUCTION =======================================

with tab_auction:
    ss = st.session_state
    if len(ss.teams) < 2:
        st.warning("Add at least two teams in the **Teams** tab before starting the auction.")
    elif not ss.players:
        st.warning("Add players in the **Players** tab before starting the auction.")
    else:
        available = [p for p in ss.players if p["status"] == "available"]

        # --- pick a player -------------------------------------------------
        if ss.current_player_id is None:
            st.subheader("Next player")
            if not available:
                st.success("🎉 Player pool is empty — auction complete! "
                           "(Unsold players can be recycled from the Players tab.)")
            else:
                c1, c2, c3 = st.columns([2, 1, 1])
                pick = c1.selectbox(
                    "Choose a player", [p["id"] for p in available],
                    format_func=lambda i: f"{get_player(i)['name']} "
                                          f"({get_player(i)['role'] or 'n/a'} · "
                                          f"base {fmt_money(get_player(i)['base_price'])})")
                c2.markdown("&nbsp;")
                if c2.button("▶️ Put on the block", type="primary", width="stretch"):
                    start_player(pick)
                    st.rerun()
                c3.markdown("&nbsp;")
                if c3.button("🎲 Random player", width="stretch"):
                    start_player(random.choice(available)["id"])
                    st.rerun()
            if ss.sale_history and st.button("↩️ Undo last sale / unsold"):
                undo_last()
                st.rerun()

        # --- bidding -------------------------------------------------------
        else:
            player = get_player(ss.current_player_id)
            bid = ss.current_bid
            cur_amount = bid["amount"] if bid else player["base_price"]

            with st.container(border=True):
                c1, c2 = st.columns([2, 1])
                with c1:
                    st.markdown(f"## 🏷️ {player['name']}")
                    meta = " · ".join(x for x in [
                        player["role"], f"rating {player['rating']}" if player["rating"] else "",
                        player["notes"]] if x)
                    if meta:
                        st.caption(meta)
                    st.markdown(f"Base price: **{fmt_money(player['base_price'])}**")
                with c2:
                    if bid:
                        st.metric("Current bid", fmt_money(bid["amount"]),
                                  delta=bid["team"])
                        st.caption(f"🔤 {amount_in_words(bid['amount'])}")
                    else:
                        st.metric("Current bid", "—", delta="awaiting first bid")

            st.markdown(f"**Bid buttons** (next bid: "
                        f"{fmt_money(cur_amount if not bid else cur_amount + increment_for(cur_amount))})")
            cols = st.columns(min(4, len(ss.teams)))
            for i, t in enumerate(ss.teams):
                tn = t["name"]
                next_amt = player["base_price"] if not bid else bid["amount"] + increment_for(bid["amount"])
                max_bid = team_max_bid(tn)
                is_leader = bool(bid and bid["team"] == tn)
                full = team_is_full(tn)
                disabled = is_leader or full or next_amt > max_bid
                with cols[i % len(cols)]:
                    label = f"**{tn}**\n\n{fmt_money(next_amt)}"
                    if full:
                        help_txt = "Squad full"
                    elif next_amt > max_bid:
                        help_txt = f"Can't afford (max bid {fmt_money(max(0, max_bid))})"
                    elif is_leader:
                        help_txt = "Already the highest bidder"
                    else:
                        help_txt = f"Purse left {fmt_money(team_purse_left(tn))} · max bid {fmt_money(max_bid)}"
                    if st.button(label, key=f"bid_{tn}", disabled=disabled,
                                 help=help_txt, width="stretch"):
                        place_bid(tn, next_amt)
                        st.rerun()
                    st.caption(("👑 leading · " if is_leader else "")
                               + f"left {fmt_money(team_purse_left(tn))}")

            with st.expander("✏️ Custom bid (jump bid)"):
                c1, c2, c3 = st.columns([2, 2, 1])
                jump_team = c1.selectbox("Team", [t["name"] for t in ss.teams], key="jump_team")
                min_jump = float(player["base_price"] if not bid
                                 else bid["amount"] + increment_for(bid["amount"]))
                jump_amt = c2.number_input("Amount", min_value=min_jump, value=min_jump,
                                           step=float(increment_for(cur_amount)))
                c2.caption(f"{fmt_money(jump_amt)} — **{amount_in_words(jump_amt)}**")
                c3.markdown("&nbsp;")
                if c3.button("Place bid", width="stretch"):
                    if team_is_full(jump_team):
                        st.error("That team's squad is full.")
                    elif jump_amt > team_max_bid(jump_team):
                        st.error(f"Exceeds team's max bid "
                                 f"({fmt_money(max(0, team_max_bid(jump_team)))}).")
                    else:
                        place_bid(jump_team, jump_amt)
                        st.rerun()

            st.divider()
            c1, c2, c3 = st.columns(3)
            if c1.button(f"✅ SOLD{' to ' + bid['team'] + ' for ' + fmt_money(bid['amount']) if bid else ''}",
                         type="primary", disabled=not bid, width="stretch"):
                sell_current()
                st.rerun()
            if c2.button("❌ Unsold", disabled=bool(bid), width="stretch",
                         help="Only when there are no bids"):
                mark_unsold()
                st.rerun()
            if c3.button("↩️ Back to pool (cancel)", width="stretch"):
                if bid:
                    log_event("UNDO", player, bid["amount"], bid["team"])
                ss.current_player_id = None
                ss.current_bid = None
                st.rerun()

            # recent bids for this player
            recent = [l for l in ss.bid_log if l["player"] == player["name"] and l["event"] == "BID"]
            if recent:
                st.caption("Bid history for this player")
                st.dataframe(pd.DataFrame(recent[::-1])[["time", "team", "amount"]],
                             width="stretch", hide_index=True)

# ====================== DASHBOARD ==========================================

with tab_dash:
    ss = st.session_state
    if not ss.teams:
        st.info("Add teams to see the dashboard.")
    else:
        summary = []
        for t in ss.teams:
            roster = team_players(t["name"])
            left = team_purse_left(t["name"])
            summary.append({
                "Team": t["name"],
                "Captain": t["captain"],
                "Players": len(roster),
                "Slots left": max(0, cfg["max_squad_size"] - len(roster)),
                "Purse": t["purse"],
                "Spent": team_spent(t["name"]),
                "Remaining": left,
                "Max next bid": max(0, team_max_bid(t["name"])),
            })
        sdf = pd.DataFrame(summary)
        st.dataframe(sdf, width="stretch", hide_index=True)

        st.divider()
        cols = st.columns(min(3, len(ss.teams)))
        for i, t in enumerate(ss.teams):
            with cols[i % len(cols)]:
                with st.container(border=True):
                    st.markdown(f"### {t['name']}")
                    st.caption(f"👑 {t['captain'] or '—'} · "
                               f"remaining {fmt_money(team_purse_left(t['name']))}")
                    roster = team_players(t["name"])
                    if roster:
                        rdf = pd.DataFrame(roster)[["name", "role", "sold_price"]]
                        rdf.columns = ["Player", "Role", "Price"]
                        st.dataframe(rdf, width="stretch", hide_index=True)
                    else:
                        st.caption("No players bought yet.")

        sold_players = [p for p in ss.players if p["status"] == "sold"]
        if sold_players:
            st.divider()
            top = max(sold_players, key=lambda p: p["sold_price"])
            c1, c2, c3 = st.columns(3)
            c1.metric("Most expensive buy",
                      top["name"], f"{fmt_money(top['sold_price'])} → {top['sold_to']}")
            c2.metric("Total spent (all teams)",
                      fmt_money(sum(p["sold_price"] for p in sold_players)))
            c3.metric("Avg price", fmt_money(
                sum(p["sold_price"] for p in sold_players) / len(sold_players)))

        # results export
        st.divider()
        if sold_players:
            out = pd.DataFrame(sold_players)[["name", "role", "base_price", "sold_to", "sold_price"]]
            out.columns = ["Player", "Role", "Base price", "Team", "Sold price"]
            st.download_button("⬇️ Download final squads (CSV)",
                               out.to_csv(index=False),
                               file_name="auction_results.csv", mime="text/csv")

# ====================== BID LOG ============================================

with tab_log:
    ss = st.session_state
    if not ss.bid_log:
        st.info("No auction activity yet — events will appear here as bidding happens.")
    else:
        c1, c2 = st.columns([3, 1])
        ev_filter = c1.multiselect("Event types", ["BID", "SOLD", "UNSOLD", "UNDO"],
                                   default=["BID", "SOLD", "UNSOLD", "UNDO"])
        rows = [l for l in ss.bid_log if l["event"] in ev_filter]
        ldf = pd.DataFrame(rows[::-1])
        st.dataframe(ldf, width="stretch", hide_index=True)
        c2.download_button("⬇️ Export log (CSV)",
                           pd.DataFrame(ss.bid_log).to_csv(index=False),
                           file_name="bid_log.csv", mime="text/csv",
                           width="stretch")
