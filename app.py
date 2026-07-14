"""
Tournament Auction Manager
A sport-agnostic Streamlit app to run and track player auctions
(badminton, cricket, football, ... any team-based tournament).

The auction is conducted CATEGORY-WISE: each category (e.g. U19, Open Men)
has its own number of players per team and its own spending cap. Money left
over in one category's cap does NOT carry into another category — but every
purchase also draws from the team's TOTAL purse, and total purse remaining
is the tiebreaker when two teams bid the same amount.

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
    "default_purse": 10000000,      # 1 Crore total purse per team
    "default_base_price": 100000,   # 1 Lakh
    # slabs: "from_amount:increment" pairs
    "increment_slabs": "0:50000, 1000000:100000, 2500000:250000",
}

# Each category: how many players every team picks in it, and the max a team
# may spend inside it. Caps are independent — leftovers do not roll over.
DEFAULT_CATEGORIES = [
    {"name": "U19", "slots_per_team": 2, "max_cap": 4000000},
    {"name": "Open Men", "slots_per_team": 4, "max_cap": 6000000},
]

PLAYER_COLUMNS = ["name", "category", "age", "base_price", "notes"]

TEAM_COLORS = [
    ("#6366f1", "#a5b4fc"), ("#f59e0b", "#fcd34d"), ("#10b981", "#6ee7b7"),
    ("#ef4444", "#fca5a5"), ("#8b5cf6", "#c4b5fd"), ("#06b6d4", "#67e8f9"),
    ("#ec4899", "#f9a8d4"), ("#84cc16", "#bef264"),
]


def init_state():
    ss = st.session_state
    ss.setdefault("config", dict(DEFAULT_CONFIG))
    ss.setdefault("categories", [dict(c) for c in DEFAULT_CATEGORIES])
    ss.setdefault("teams", [])      # {name, captain, purse}
    ss.setdefault("players", [])    # {id, name, category, age, base_price, notes,
                                    #  status: available|sold|unsold, sold_to, sold_price}
    ss.setdefault("bid_log", [])    # every bid + sold/unsold/tie event
    ss.setdefault("current_category", None)
    ss.setdefault("current_player_id", None)
    ss.setdefault("current_bid", None)      # {amount, team} or None
    ss.setdefault("sale_history", [])       # stack for undo (sold/unsold events)
    ss.setdefault("next_player_id", 1)
    ss.setdefault("last_sold_banner", None)  # {player, team, amount} for celebration


def team_color(team_name):
    names = [t["name"] for t in st.session_state.teams]
    idx = names.index(team_name) if team_name in names else 0
    return TEAM_COLORS[idx % len(TEAM_COLORS)]


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


def fmt_short(amount):
    """Compact Indian form: 4000000 -> '₹40L', 12500000 -> '₹1.25Cr'."""
    cur = st.session_state.config["currency"]
    amount = float(amount)
    sign = "-" if amount < 0 else ""
    a = abs(amount)
    if a >= 10**7:
        v = a / 10**7
        return f"{sign}{cur}{v:.2f}".rstrip("0").rstrip(".") + "Cr"
    if a >= 10**5:
        v = a / 10**5
        return f"{sign}{cur}{v:.2f}".rstrip("0").rstrip(".") + "L"
    if a >= 1000:
        v = a / 1000
        return f"{sign}{cur}{v:.1f}".rstrip("0").rstrip(".") + "K"
    return f"{sign}{cur}{fmt_indian(a)}"


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


def get_category(name):
    for c in st.session_state.categories:
        if c["name"] == name:
            return c
    return None


def category_names():
    return [c["name"] for c in st.session_state.categories]


def team_players(team_name, category=None):
    return [p for p in st.session_state.players
            if p["status"] == "sold" and p["sold_to"] == team_name
            and (category is None or p["category"] == category)]


def team_spent(team_name, category=None):
    return sum(p["sold_price"] for p in team_players(team_name, category))


def team_total_left(team_name):
    """TOTAL purse remaining — the tiebreaker number."""
    team = get_team(team_name)
    return team["purse"] - team_spent(team_name)


def team_cap_left(team_name, category):
    """Spending room left inside a category's cap (does not roll over)."""
    cat = get_category(category)
    if cat is None:
        return team_total_left(team_name)
    return cat["max_cap"] - team_spent(team_name, category)


def team_spendable(team_name, category):
    """What a team can actually spend right now in this category:
    limited by BOTH the category cap and the total purse."""
    return min(team_cap_left(team_name, category), team_total_left(team_name))


def team_slots_left(team_name, category):
    cat = get_category(category)
    if cat is None:
        return 0
    return cat["slots_per_team"] - len(team_players(team_name, category))


def team_max_bid(team_name, category):
    """Max a team can bid on the current player while still affording base
    price for the remaining slots it must fill in this category."""
    cfg = st.session_state.config
    spendable = team_spendable(team_name, category)
    must_still_buy = max(0, team_slots_left(team_name, category) - 1)  # after this one
    reserve = must_still_buy * cfg["default_base_price"]
    return spendable - reserve


def category_is_done(category):
    """Every team filled its slots, or no available players remain in it."""
    ss = st.session_state
    avail = [p for p in ss.players
             if p["status"] == "available" and p["category"] == category]
    all_full = ss.teams and all(team_slots_left(t["name"], category) <= 0
                                for t in ss.teams)
    return not avail or all_full


def log_event(event, player, amount=None, team=None, note=""):
    st.session_state.bid_log.append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "event": event,          # BID | TIE-WIN | SOLD | UNSOLD | UNDO
        "category": player["category"] if player else "",
        "player": player["name"] if player else "",
        "team": team or "",
        "amount": amount if amount is not None else "",
        "note": note,
    })


# ---------------------------------------------------------------------------
# Auction actions
# ---------------------------------------------------------------------------

def start_player(pid):
    ss = st.session_state
    ss.current_player_id = pid
    ss.current_bid = None
    ss.last_sold_banner = None


def place_bid(team_name, amount):
    """Place a bid. Returns (ok, message).

    Tiebreaker rule: a team may MATCH the current highest bid (same amount).
    The tie is resolved in favour of the team with the higher TOTAL purse
    remaining — category leftovers don't count, only overall money left.
    """
    ss = st.session_state
    player = get_player(ss.current_player_id)
    bid = ss.current_bid

    if bid and team_name == bid["team"]:
        return False, "Already the highest bidder."

    if bid and amount == bid["amount"]:
        challenger_left = team_total_left(team_name)
        leader_left = team_total_left(bid["team"])
        if challenger_left > leader_left:
            ss.current_bid = {"amount": amount, "team": team_name}
            note = (f"Tie at {fmt_money(amount)} — {team_name} wins on total purse "
                    f"({fmt_short(challenger_left)} vs {fmt_short(leader_left)})")
            log_event("TIE-WIN", player, amount, team_name, note)
            return True, note
        note = (f"Tie at {fmt_money(amount)} — {bid['team']} keeps the bid on total "
                f"purse ({fmt_short(leader_left)} vs {fmt_short(challenger_left)})")
        log_event("TIE-LOST", player, amount, team_name, note)
        return False, note

    if bid and amount < bid["amount"]:
        return False, "Bid must be at least the current highest bid."

    ss.current_bid = {"amount": amount, "team": team_name}
    log_event("BID", player, amount, team_name)
    return True, ""


def sell_current():
    ss = st.session_state
    player = get_player(ss.current_player_id)
    bid = ss.current_bid
    player["status"] = "sold"
    player["sold_to"] = bid["team"]
    player["sold_price"] = bid["amount"]
    log_event("SOLD", player, bid["amount"], bid["team"])
    ss.sale_history.append({"type": "sold", "player_id": player["id"]})
    ss.last_sold_banner = {"player": player["name"], "team": bid["team"],
                           "amount": bid["amount"], "category": player["category"]}
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
    ss.last_sold_banner = None
    ss.current_category = player["category"]
    # put the player back on the block
    start_player(player["id"])


def add_player(name, category, age, base_price, notes="", status="available"):
    ss = st.session_state
    ss.players.append({
        "id": ss.next_player_id,
        "name": str(name).strip(),
        "category": str(category).strip(),
        "age": str(age).strip(),
        "base_price": float(base_price),
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
        "categories": ss.categories,
        "teams": ss.teams,
        "players": ss.players,
        "bid_log": ss.bid_log,
        "sale_history": ss.sale_history,
        "next_player_id": ss.next_player_id,
    }, indent=2)


def import_state(data):
    ss = st.session_state
    ss.config = {**DEFAULT_CONFIG, **data.get("config", {})}
    ss.categories = data.get("categories", [dict(c) for c in DEFAULT_CATEGORIES])
    ss.teams = data.get("teams", [])
    players = data.get("players", [])
    for p in players:  # migrate old saves: role->category, rating->age
        p.setdefault("category", p.pop("role", ""))
        p.setdefault("age", p.pop("rating", ""))
    ss.players = players
    ss.bid_log = data.get("bid_log", [])
    ss.sale_history = data.get("sale_history", [])
    ss.next_player_id = data.get(
        "next_player_id",
        max([p["id"] for p in ss.players], default=0) + 1,
    )
    ss.current_category = None
    ss.current_player_id = None
    ss.current_bid = None


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

init_state()
cfg = st.session_state.config

st.markdown("""
<style>
  .block-container { padding-top: 2.2rem; }
  .hero {
    background: linear-gradient(135deg, #1e1b4b 0%, #4338ca 55%, #7c3aed 100%);
    border-radius: 18px; padding: 22px 30px; color: #fff; margin-bottom: 6px;
    box-shadow: 0 8px 26px rgba(67,56,202,.35);
  }
  .hero h1 { margin: 0; font-size: 2rem; color: #fff; }
  .hero .sub { opacity: .85; margin-top: 4px; font-size: .95rem; }
  .player-card {
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 60%, #334155 100%);
    border-radius: 18px; padding: 26px 30px; color: #fff;
    border: 1px solid rgba(255,255,255,.12);
    box-shadow: 0 10px 30px rgba(2,6,23,.45);
  }
  .player-card .pname { font-size: 2.3rem; font-weight: 800; line-height: 1.1; }
  .player-card .pmeta { opacity: .8; margin-top: 6px; font-size: 1rem; }
  .bid-big { font-size: 2.6rem; font-weight: 900; color: #fbbf24; line-height: 1.05; }
  .bid-team { font-size: 1.15rem; font-weight: 700; margin-top: 2px; }
  .bid-words { opacity: .75; font-size: .85rem; margin-top: 4px; }
  .chip {
    display: inline-block; padding: 3px 12px; border-radius: 999px;
    font-size: .8rem; font-weight: 700; margin-right: 6px; margin-top: 6px;
    background: rgba(255,255,255,.14); color: #fff;
    border: 1px solid rgba(255,255,255,.2);
  }
  .cat-chip {
    display: inline-block; padding: 4px 14px; border-radius: 999px;
    font-size: .85rem; font-weight: 700; margin: 2px 6px 2px 0;
  }
  .sold-banner {
    background: linear-gradient(135deg, #064e3b, #059669);
    border-radius: 16px; padding: 18px 26px; color: #fff; margin: 8px 0 14px 0;
    font-size: 1.25rem; font-weight: 800; text-align: center;
    box-shadow: 0 8px 24px rgba(5,150,105,.4);
  }
  .team-card {
    border-radius: 16px; padding: 16px 18px; color: #fff; margin-bottom: 12px;
    box-shadow: 0 6px 18px rgba(2,6,23,.25);
  }
  .team-card .tname { font-size: 1.25rem; font-weight: 800; }
  .team-card .tmoney { font-size: 1.6rem; font-weight: 900; margin-top: 2px; }
  .team-card .tsub { opacity: .85; font-size: .82rem; }
  .capbar { background: rgba(255,255,255,.22); border-radius: 999px; height: 8px;
            margin-top: 6px; overflow: hidden; }
  .capbar > div { height: 100%; background: #fff; border-radius: 999px; }
  div[data-testid="stMetricValue"] { font-weight: 800; }
</style>
""", unsafe_allow_html=True)

cats_line = " · ".join(
    f"{c['name']}: {c['slots_per_team']}/team, cap {fmt_short(c['max_cap'])}"
    for c in st.session_state.categories)
st.markdown(f"""
<div class="hero">
  <h1>🔨 {cfg['tournament_name']} — Live Auction</h1>
  <div class="sub">{cfg['sport']} · total purse {fmt_short(cfg['default_purse'])} per team
   &nbsp;|&nbsp; {cats_line}</div>
</div>
""", unsafe_allow_html=True)

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
        st.subheader("💰 Total purse left")
        st.caption("Tiebreaker: on equal bids, higher total purse wins.")
        for t in sorted(st.session_state.teams,
                        key=lambda t: -team_total_left(t["name"])):
            left = team_total_left(t["name"])
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
        purse = c2.number_input("Total purse per team", min_value=0.0,
                                value=float(cfg["default_purse"]), step=100000.0)
        c2.caption(f"{fmt_money(purse)} — **{amount_in_words(purse)}**")
        base = c3.number_input("Default base price", min_value=0.0,
                               value=float(cfg["default_base_price"]), step=10000.0)
        c3.caption(f"{fmt_money(base)} — **{amount_in_words(base)}**")
        slabs = st.text_input(
            "Bid increment slabs (`from:increment`, comma separated)",
            cfg["increment_slabs"],
            help="e.g. `0:50000, 1000000:100000` → +50K below 10L, +1L from 10L",
        )
        if st.button("Save settings", type="primary"):
            cfg.update({
                "tournament_name": name, "sport": sport, "currency": currency,
                "default_purse": purse, "default_base_price": base,
                "increment_slabs": slabs,
            })
            st.success("Settings saved.")
            st.rerun()

    st.subheader("🏷️ Categories")
    st.caption("The auction runs one category at a time. Each category sets how many "
               "players every team must pick and the max a team can spend in it. "
               "**Unspent cap does not carry over to other categories.**")
    cat_df = pd.DataFrame(st.session_state.categories)
    edited = st.data_editor(
        cat_df,
        num_rows="dynamic",
        width="stretch",
        column_config={
            "name": st.column_config.TextColumn("Category", required=True),
            "slots_per_team": st.column_config.NumberColumn(
                "Players per team", min_value=1, step=1, required=True),
            "max_cap": st.column_config.NumberColumn(
                "Max cap per team", min_value=0, step=100000, required=True,
                help="Maximum a team may spend inside this category"),
        },
        key="cat_editor",
    )
    total_caps = float(edited["max_cap"].fillna(0).sum()) if not edited.empty else 0
    st.caption(f"Sum of category caps: **{fmt_money(total_caps)}** · "
               f"total purse: **{fmt_money(cfg['default_purse'])}** — "
               + ("caps exceed the purse, so the total purse will bind first. ✅ That's "
                  "exactly what makes the tiebreaker matter."
                  if total_caps > cfg["default_purse"] else
                  "caps fit within the purse."))
    if st.button("Save categories", type="primary"):
        cats = []
        seen = set()
        for _, row in edited.iterrows():
            nm = str(row.get("name") or "").strip()
            if not nm or nm.lower() in seen:
                continue
            seen.add(nm.lower())
            cats.append({
                "name": nm,
                "slots_per_team": int(row.get("slots_per_team") or 1),
                "max_cap": float(row.get("max_cap") or 0),
            })
        if not cats:
            st.error("Add at least one category.")
        else:
            st.session_state.categories = cats
            st.success("Categories saved.")
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
        st.session_state.current_category = None
        st.session_state.last_sold_banner = None
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
        t_purse = c3.number_input("Total purse", min_value=0.0,
                                  value=float(cfg["default_purse"]), step=100000.0,
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
            color, _ = team_color(t["name"])
            with st.container(border=True):
                c1, c2, c3, c4, c5 = st.columns([3, 3, 2, 2, 1])
                c1.markdown(
                    f"<span style='color:{color};font-weight:800'>⬤</span> **{t['name']}**",
                    unsafe_allow_html=True)
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
    cat_options = category_names()

    up_col, man_col = st.columns(2)
    with up_col:
        st.markdown("**Upload CSV**")
        template = pd.DataFrame(
            [{"name": "Player One", "category": cat_options[0] if cat_options else "U19",
              "age": 17, "base_price": cfg["default_base_price"], "notes": ""}],
            columns=PLAYER_COLUMNS)
        st.download_button("Download CSV template",
                           template.to_csv(index=False),
                           file_name="players_template.csv", mime="text/csv")
        up = st.file_uploader("CSV with columns: name, category, age, base_price, notes "
                              "(only `name` is required; `role`/`rating` from old files "
                              "are read as category/age)", type="csv")
        if up is not None and st.button("Import players"):
            df = pd.read_csv(up)
            df.columns = [c.strip().lower() for c in df.columns]
            # accept legacy column names
            if "category" not in df.columns and "role" in df.columns:
                df = df.rename(columns={"role": "category"})
            if "age" not in df.columns and "rating" in df.columns:
                df = df.rename(columns={"rating": "age"})
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
                        "" if pd.isna(row.get("category")) else row.get("category"),
                        "" if pd.isna(row.get("age")) else row.get("age"),
                        bp,
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
            if cat_options:
                p_cat = c1.selectbox("Category", cat_options, key="add_player_cat")
            else:
                p_cat = c1.text_input("Category", key="add_player_cat")
            p_age = c2.text_input("Age", key="add_player_age")
            c1, c2 = st.columns(2)
            p_base = c1.number_input("Base price", min_value=0.0,
                                     value=float(cfg["default_base_price"]), step=10000.0,
                                     key="add_player_base")
            c1.caption(f"{fmt_money(p_base)} — **{amount_in_words(p_base)}**")
            p_notes = c2.text_input("Notes", key="add_player_notes")
            if st.button("➕ Add player"):
                if not p_name.strip():
                    st.error("Name is required.")
                elif p_name.strip().lower() in {p["name"].lower() for p in st.session_state.players}:
                    st.error("A player with that name already exists.")
                else:
                    add_player(p_name, p_cat, p_age, p_base, p_notes)
                    for k in ("add_player_name", "add_player_cat", "add_player_age",
                              "add_player_base", "add_player_notes"):
                        del st.session_state[k]
                    st.rerun()

    st.divider()
    if st.session_state.players:
        f1, f2 = st.columns(2)
        status_filter = f1.multiselect(
            "Filter by status", ["available", "sold", "unsold"],
            default=["available", "sold", "unsold"])
        all_cats = sorted({p["category"] for p in st.session_state.players if p["category"]})
        cat_filter = f2.multiselect("Filter by category", all_cats, default=all_cats)
        rows = [p for p in st.session_state.players
                if p["status"] in status_filter
                and (p["category"] in cat_filter or not p["category"])]
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df[["id", "name", "category", "age", "base_price",
                     "status", "sold_to", "sold_price", "notes"]]
            st.dataframe(df, width="stretch", hide_index=True)

        # warn about players whose category isn't configured
        unknown = sorted({p["category"] for p in st.session_state.players
                          if p["category"] and get_category(p["category"]) is None})
        if unknown:
            st.warning("These player categories are not configured in Setup → Categories: "
                       + ", ".join(f"`{c}`" for c in unknown)
                       + ". They won't appear in the live auction until added.")

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
    elif not ss.categories:
        st.warning("Configure categories in the **Setup** tab first.")
    else:
        # --- celebration banner from the previous sale ----------------------
        if ss.last_sold_banner and ss.current_player_id is None:
            b = ss.last_sold_banner
            color, _ = team_color(b["team"])
            st.markdown(
                f"<div class='sold-banner'>🎉 SOLD! &nbsp;{b['player']} "
                f"({b['category']}) → <span style='color:{'#fff'}'>{b['team']}</span> "
                f"for {fmt_money(b['amount'])}</div>",
                unsafe_allow_html=True)

        # --- category selector ----------------------------------------------
        st.markdown("#### 1️⃣ Category on the block")
        chip_cols = st.columns(len(ss.categories))
        for i, cat in enumerate(ss.categories):
            cname = cat["name"]
            avail_n = sum(1 for p in ss.players
                          if p["status"] == "available" and p["category"] == cname)
            sold_n = sum(1 for p in ss.players
                         if p["status"] == "sold" and p["category"] == cname)
            done = category_is_done(cname)
            active = ss.current_category == cname
            label = (("✅ " if done else "🟢 " if active else "") + cname
                     + f"  ·  {avail_n} left / {sold_n} sold")
            if chip_cols[i].button(label, key=f"cat_{cname}", width="stretch",
                                   type="primary" if active else "secondary",
                                   disabled=bool(ss.current_player_id)
                                   and ss.current_category != cname):
                ss.current_category = cname
                st.rerun()

        if ss.current_category is None:
            st.info("👆 Pick a category to auction. Finish a category before moving on — "
                    "leftover cap money does **not** carry over.")
        else:
            cat = get_category(ss.current_category)
            cname = cat["name"]

            # per-team category status strip
            st.markdown(f"#### 2️⃣ {cname} — team status "
                        f"(cap {fmt_short(cat['max_cap'])}, "
                        f"{cat['slots_per_team']} players per team)")
            tcols = st.columns(min(4, len(ss.teams)))
            for i, t in enumerate(ss.teams):
                tn = t["name"]
                color, light = team_color(tn)
                cap_left = team_cap_left(tn, cname)
                spendable = team_spendable(tn, cname)
                slots = team_slots_left(tn, cname)
                filled = cat["slots_per_team"] - slots
                pct = max(0.0, min(1.0, cap_left / cat["max_cap"])) if cat["max_cap"] else 0
                with tcols[i % len(tcols)]:
                    st.markdown(f"""
                    <div class="team-card" style="background:linear-gradient(135deg,{color},{color}cc)">
                      <div class="tname">{tn}</div>
                      <div class="tmoney">{fmt_short(spendable)}</div>
                      <div class="tsub">spendable now in {cname}</div>
                      <div class="capbar"><div style="width:{pct*100:.0f}%"></div></div>
                      <div class="tsub" style="margin-top:6px">
                        cap left {fmt_short(cap_left)} · total left {fmt_short(team_total_left(tn))}<br>
                        slots {'●' * filled}{'○' * max(0, slots)} {filled}/{cat['slots_per_team']}
                      </div>
                    </div>""", unsafe_allow_html=True)

            available = [p for p in ss.players
                         if p["status"] == "available" and p["category"] == cname]

            # --- pick a player -------------------------------------------------
            if ss.current_player_id is None:
                st.markdown("#### 3️⃣ Next player")
                if category_is_done(cname):
                    st.success(f"🎉 **{cname} is complete!** "
                               + ("All teams have filled their slots. " if available else
                                  "No available players remain (recycle unsold from the "
                                  "Players tab if needed). ")
                               + "Pick the next category above — leftover cap money "
                                 "stays behind.")
                else:
                    c1, c2, c3 = st.columns([2, 1, 1])
                    pick = c1.selectbox(
                        "Choose a player", [p["id"] for p in available],
                        format_func=lambda i: f"{get_player(i)['name']} "
                                              f"(age {get_player(i)['age'] or '—'} · "
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
                next_amt = (player["base_price"] if not bid
                            else bid["amount"] + increment_for(bid["amount"]))

                meta_bits = [f"🏷️ {player['category']}"]
                if player["age"]:
                    meta_bits.append(f"🎂 Age {player['age']}")
                meta_bits.append(f"Base {fmt_money(player['base_price'])}")
                if player["notes"]:
                    meta_bits.append(player["notes"])
                if bid:
                    bcolor, _ = team_color(bid["team"])
                    bid_html = (f"<div class='bid-big'>{fmt_money(bid['amount'])}</div>"
                                f"<div class='bid-team' style='color:{bcolor}'>"
                                f"👑 {bid['team']}</div>"
                                f"<div class='bid-words'>{amount_in_words(bid['amount'])}</div>")
                else:
                    bid_html = ("<div class='bid-big' style='opacity:.5'>—</div>"
                                "<div class='bid-words'>awaiting first bid · starts at "
                                f"{fmt_money(player['base_price'])}</div>")
                st.markdown(f"""
                <div class="player-card">
                  <div style="display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap">
                    <div>
                      <div class="pname">{player['name']}</div>
                      <div class="pmeta">{' · '.join(meta_bits)}</div>
                      <span class="chip">{cname}</span>
                      <span class="chip">next bid {fmt_money(next_amt)}</span>
                    </div>
                    <div style="text-align:right">{bid_html}</div>
                  </div>
                </div>
                """, unsafe_allow_html=True)
                st.markdown("")

                cols = st.columns(min(4, len(ss.teams)))
                for i, t in enumerate(ss.teams):
                    tn = t["name"]
                    max_bid = team_max_bid(tn, cname)
                    is_leader = bool(bid and bid["team"] == tn)
                    no_slots = team_slots_left(tn, cname) <= 0
                    disabled = is_leader or no_slots or next_amt > max_bid
                    with cols[i % len(cols)]:
                        label = f"**{tn}**\n\n{fmt_money(next_amt)}"
                        if no_slots:
                            help_txt = f"No {cname} slots left"
                        elif next_amt > max_bid:
                            help_txt = f"Can't afford (max bid {fmt_money(max(0, max_bid))})"
                        elif is_leader:
                            help_txt = "Already the highest bidder"
                        else:
                            help_txt = (f"Spendable in {cname}: "
                                        f"{fmt_money(team_spendable(tn, cname))} · "
                                        f"max bid {fmt_money(max_bid)}")
                        if st.button(label, key=f"bid_{tn}", disabled=disabled,
                                     help=help_txt, width="stretch"):
                            place_bid(tn, next_amt)
                            st.rerun()
                        st.caption(("👑 leading · " if is_leader else "")
                                   + f"cat {fmt_short(max(0, team_cap_left(tn, cname)))} · "
                                     f"total {fmt_short(team_total_left(tn))}")

                with st.expander("✏️ Custom bid / ⚖️ Match bid (tiebreaker)"):
                    st.caption("A team may **match** the current bid (same amount). "
                               "The tie goes to the team with the higher **total** purse "
                               "remaining — category leftovers don't count.")
                    c1, c2, c3 = st.columns([2, 2, 1])
                    jump_team = c1.selectbox("Team", [t["name"] for t in ss.teams],
                                             key="jump_team")
                    min_jump = float(cur_amount if bid else player["base_price"])
                    jump_amt = c2.number_input("Amount", min_value=min_jump,
                                               value=float(next_amt),
                                               step=float(increment_for(cur_amount)))
                    c2.caption(f"{fmt_money(jump_amt)} — **{amount_in_words(jump_amt)}**")
                    c3.markdown("&nbsp;")
                    if c3.button("Place bid", width="stretch"):
                        if team_slots_left(jump_team, cname) <= 0:
                            st.error(f"{jump_team} has no {cname} slots left.")
                        elif jump_amt > team_max_bid(jump_team, cname):
                            st.error(f"Exceeds {jump_team}'s max bid "
                                     f"({fmt_money(max(0, team_max_bid(jump_team, cname)))}).")
                        else:
                            ok, msg = place_bid(jump_team, jump_amt)
                            if msg:
                                (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()

                st.divider()
                c1, c2, c3 = st.columns(3)
                if c1.button(f"✅ SOLD{' to ' + bid['team'] + ' for ' + fmt_money(bid['amount']) if bid else ''}",
                             type="primary", disabled=not bid, width="stretch"):
                    sell_current()
                    st.balloons()
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
                recent = [l for l in ss.bid_log
                          if l["player"] == player["name"]
                          and l["event"] in ("BID", "TIE-WIN", "TIE-LOST")]
                if recent:
                    st.caption("Bid history for this player")
                    st.dataframe(pd.DataFrame(recent[::-1])[["time", "event", "team", "amount"]],
                                 width="stretch", hide_index=True)

# ====================== DASHBOARD ==========================================

with tab_dash:
    ss = st.session_state
    if not ss.teams:
        st.info("Add teams to see the dashboard.")
    else:
        cat_names = category_names()

        # headline metrics
        sold_players = [p for p in ss.players if p["status"] == "sold"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Players sold", len(sold_players))
        m2.metric("Total spent", fmt_short(sum(p["sold_price"] for p in sold_players)) if sold_players else "—")
        if sold_players:
            top = max(sold_players, key=lambda p: p["sold_price"])
            m3.metric("Top buy", top["name"], f"{fmt_short(top['sold_price'])} → {top['sold_to']}")
            m4.metric("Avg price", fmt_short(
                sum(p["sold_price"] for p in sold_players) / len(sold_players)))
        else:
            m3.metric("Top buy", "—")
            m4.metric("Avg price", "—")

        st.divider()

        # summary table: per team, per category
        summary = []
        for t in ss.teams:
            tn = t["name"]
            row = {"Team": tn, "Captain": t["captain"]}
            for cn in cat_names:
                cat = get_category(cn)
                row[f"{cn} filled"] = (f"{len(team_players(tn, cn))}/"
                                       f"{cat['slots_per_team']}")
                row[f"{cn} cap left"] = fmt_short(max(0, team_cap_left(tn, cn)))
            row["Spent"] = fmt_short(team_spent(tn))
            row["Total left ⚖️"] = fmt_short(team_total_left(tn))
            summary.append(row)
        st.dataframe(pd.DataFrame(summary), width="stretch", hide_index=True)
        st.caption("⚖️ *Total left* is the tiebreaker — on equal bids, "
                   "the team with more total purse remaining wins.")

        st.divider()

        # team squad cards
        cols = st.columns(min(3, len(ss.teams)))
        for i, t in enumerate(ss.teams):
            tn = t["name"]
            color, light = team_color(tn)
            with cols[i % len(cols)]:
                total_left = team_total_left(tn)
                pct = max(0.0, min(1.0, total_left / t["purse"])) if t["purse"] else 0
                cat_chips = "".join(
                    f"<span class='cat-chip' style='background:{color}22;color:{color};"
                    f"border:1px solid {color}66'>"
                    f"{cn} {len(team_players(tn, cn))}/{get_category(cn)['slots_per_team']}"
                    f" · {fmt_short(max(0, team_cap_left(tn, cn)))} left</span>"
                    for cn in cat_names)
                st.markdown(f"""
                <div class="team-card" style="background:linear-gradient(135deg,{color},{color}b0)">
                  <div class="tname">{tn}</div>
                  <div class="tsub">👑 {t['captain'] or '—'}</div>
                  <div class="tmoney">{fmt_short(total_left)}</div>
                  <div class="tsub">total purse remaining of {fmt_short(t['purse'])}</div>
                  <div class="capbar"><div style="width:{pct*100:.0f}%"></div></div>
                </div>""", unsafe_allow_html=True)
                st.markdown(cat_chips, unsafe_allow_html=True)
                roster = team_players(tn)
                if roster:
                    roster = sorted(roster, key=lambda p: (p["category"], -p["sold_price"]))
                    rdf = pd.DataFrame(roster)[["name", "category", "age", "sold_price"]]
                    rdf["sold_price"] = rdf["sold_price"].map(fmt_short)
                    rdf.columns = ["Player", "Category", "Age", "Price"]
                    st.dataframe(rdf, width="stretch", hide_index=True)
                else:
                    st.caption("No players bought yet.")

        # spend chart
        if sold_players:
            st.divider()
            st.markdown("##### 💸 Spend by team & category")
            chart_rows = []
            for t in ss.teams:
                for cn in cat_names:
                    chart_rows.append({"Team": t["name"], "Category": cn,
                                       "Spent": team_spent(t["name"], cn)})
            cdf = pd.DataFrame(chart_rows)
            pivot = cdf.pivot(index="Team", columns="Category", values="Spent")
            st.bar_chart(pivot, stack=True)

        # results export
        st.divider()
        if sold_players:
            out = pd.DataFrame(sold_players)[
                ["name", "category", "age", "base_price", "sold_to", "sold_price"]]
            out.columns = ["Player", "Category", "Age", "Base price", "Team", "Sold price"]
            st.download_button("⬇️ Download final squads (CSV)",
                               out.to_csv(index=False),
                               file_name="auction_results.csv", mime="text/csv")

# ====================== BID LOG ============================================

with tab_log:
    ss = st.session_state
    if not ss.bid_log:
        st.info("No auction activity yet — events will appear here as bidding happens.")
    else:
        all_events = ["BID", "TIE-WIN", "TIE-LOST", "SOLD", "UNSOLD", "UNDO"]
        c1, c2 = st.columns([3, 1])
        ev_filter = c1.multiselect("Event types", all_events, default=all_events)
        rows = [l for l in ss.bid_log if l["event"] in ev_filter]
        ldf = pd.DataFrame(rows[::-1])
        st.dataframe(ldf, width="stretch", hide_index=True)
        c2.download_button("⬇️ Export log (CSV)",
                           pd.DataFrame(ss.bid_log).to_csv(index=False),
                           file_name="bid_log.csv", mime="text/csv",
                           width="stretch")
