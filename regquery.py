"""
regquery.py - the Streamlit dashboard.

Reads the scans that scanner.py sends to Supabase and shows them with
charts. Works the same on your PC and on Streamlit Cloud.

Run it with:  python -m streamlit run regquery.py
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import streamlit as st

# ---------- Page setup ----------
st.set_page_config(page_title="Software Scanner", page_icon="🛡️", layout="wide")
st.title("🛡️ Software Scanner")
st.write("Live software scans from your Windows PCs, stored in Supabase.")

# ---------- Supabase connection ----------
# The URL and key come from Streamlit secrets, never from the code itself
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"].rstrip("/")
    PUBLISHABLE_KEY = st.secrets["SUPABASE_PUBLISHABLE_KEY"]
except Exception:
    st.error("Supabase secrets are missing. Add SUPABASE_URL and "
             "SUPABASE_PUBLISHABLE_KEY to .streamlit/secrets.toml, or to the "
             "app's Secrets on Streamlit Cloud.")
    st.stop()


def supabase_get(path):
    """Read data from the Supabase REST API and return it as Python lists/dicts."""
    request = urllib.request.Request(
        SUPABASE_URL + "/rest/v1/" + path,
        headers={"apikey": PUBLISHABLE_KEY},  # Supabase keys go in the apikey header
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


# ---------- Small helpers ----------
def seconds_since(timestamp):
    """How many seconds ago a Supabase timestamp was."""
    then = datetime.fromisoformat(timestamp)
    return (datetime.now(timezone.utc) - then).total_seconds()


def time_ago(timestamp):
    """Turn a timestamp into friendly text like '12 seconds ago'."""
    seconds = int(seconds_since(timestamp))
    if seconds < 60:
        amount, unit = seconds, "second"
    elif seconds < 3600:
        amount, unit = seconds // 60, "minute"
    elif seconds < 86400:
        amount, unit = seconds // 3600, "hour"
    else:
        amount, unit = seconds // 86400, "day"
    # add an "s" unless it's exactly 1, e.g. "1 minute" but "5 minutes"
    return f"{amount} {unit}{'' if amount == 1 else 's'} ago"


# ---------- Sidebar ----------
st.sidebar.header("Blacklist settings")
words_text = st.sidebar.text_input("Blacklist words (comma separated)", "vpn, torrent")

# Turn "vpn, torrent" into ["vpn", "torrent"]
blacklist_words = []
for word in words_text.split(","):
    word = word.strip().lower()
    if word:
        blacklist_words.append(word)


def is_blacklisted(name):
    """Return True if any blacklist word appears in the name."""
    for word in blacklist_words:
        if word in name.lower():
            return True
    return False


st.sidebar.header("Live refresh")
auto_refresh = st.sidebar.toggle("Auto refresh", value=True)
seconds = st.sidebar.number_input("Dashboard refreshes every (seconds)", min_value=10, value=60, step=10)

# Must match the Task Scheduler schedule (every 12 hours)
task_hours = st.sidebar.number_input("Scanner task runs every (hours)", min_value=1, value=12, step=1)

# ---------- Pick a computer ----------
try:
    computers = supabase_get("computers?select=computer_name&order=last_scan.desc")
except urllib.error.HTTPError as error:
    st.error(f"Supabase error {error.code}: {error.read().decode()}")
    st.stop()
except urllib.error.URLError as error:
    st.error(f"Could not reach Supabase: {error.reason}")
    st.stop()

if not computers:
    st.info("No scans yet. Start **scanner.py** on your Windows PC, "
            "then reload this page.")
    st.stop()

computer_names = [row["computer_name"] for row in computers]
computer = st.sidebar.selectbox("Computer", computer_names)


# ---------- The dashboard (numbers, charts, table) ----------
def show_dashboard(software, history):
    blacklisted = [name for name in software if is_blacklisted(name)]
    clean_count = len(software) - len(blacklisted)

    # Summary numbers
    col1, col2, col3 = st.columns(3)
    col1.metric("Total software", len(software))
    col2.metric("Blacklisted", len(blacklisted))
    col3.metric("Clean", clean_count)

    if blacklisted:
        st.error("⚠️ Blacklisted software found: " + ", ".join(blacklisted))
    else:
        st.success("✅ No blacklisted software found.")

    # Charts
    st.subheader("Charts")
    chart1, chart2 = st.columns(2)

    with chart1:
        st.write("**Clean vs Blacklisted**")
        donut_chart = {
            "data": {"values": [
                {"Status": "Clean", "Count": clean_count},
                {"Status": "Blacklisted", "Count": len(blacklisted)},
            ]},
            "mark": {"type": "arc", "innerRadius": 60},
            "encoding": {
                "theta": {"field": "Count", "type": "quantitative"},
                "color": {
                    "field": "Status",
                    "type": "nominal",
                    "scale": {"domain": ["Clean", "Blacklisted"], "range": ["#2ecc71", "#e74c3c"]},
                },
                "tooltip": [{"field": "Status"}, {"field": "Count"}],
            },
        }
        st.vega_lite_chart(donut_chart)

    with chart2:
        st.write("**Matches per blacklist word**")
        match_counts = []
        for word in blacklist_words:
            count = 0
            for name in software:
                if word in name.lower():
                    count += 1
            match_counts.append(count)
        st.bar_chart({"Word": blacklist_words, "Matches": match_counts},
                     x="Word", y="Matches", color="#e74c3c")

    percent = len(blacklisted) / len(software) if software else 0
    st.write(f"**{percent:.1%} of software is blacklisted**")
    st.progress(percent)

    # History chart: one point for every time the software list changed
    st.write("**Software count over time**")
    if len(history) >= 2:
        times, totals, flagged = [], [], []
        for scan in reversed(history):  # oldest first
            times.append(datetime.fromisoformat(scan["scanned_at"]))
            totals.append(len(scan["software"]))
            flagged.append(sum(1 for name in scan["software"] if is_blacklisted(name)))
        st.line_chart({"Time": times, "Total": totals, "Blacklisted": flagged},
                      x="Time", y=["Total", "Blacklisted"], color=["#3498db", "#e74c3c"])
    else:
        st.caption("This chart appears after the software list changes at least once.")

    # Search and filter
    st.subheader("Software list")
    search = st.text_input("Search by name")
    only_blacklisted = st.checkbox("Show only blacklisted")

    rows = []
    for name in software:
        if search.lower() not in name.lower():
            continue  # doesn't match the search box
        if only_blacklisted and not is_blacklisted(name):
            continue  # filter is on and this one is clean
        status = "🚫 Blacklisted" if is_blacklisted(name) else "✅ OK"
        rows.append({"Software": name, "Status": status})

    st.write(f"Showing {len(rows)} of {len(software)}")
    st.dataframe(rows, hide_index=True)

    # Download buttons
    st.subheader("Download")
    col1, col2 = st.columns(2)
    col1.download_button("⬇️ softwares.txt", "\n".join(software), file_name="softwares.txt")
    col2.download_button("⬇️ blacklist_softwares.txt", "\n".join(blacklisted),
                         file_name="blacklist_softwares.txt")


# ---------- Live view: re-reads Supabase on a timer ----------
refresh_every = seconds if auto_refresh else None  # None = no automatic refresh


@st.fragment(run_every=refresh_every)
def live_view():
    name = urllib.parse.quote(computer)
    try:
        latest = supabase_get(f"computers?computer_name=eq.{name}&select=software,last_scan")
        history = supabase_get(f"scan_history?computer_name=eq.{name}"
                               "&select=software,scanned_at&order=scanned_at.desc&limit=50")
    except urllib.error.URLError as error:
        st.error(f"Could not read from Supabase: {error}")
        return

    if not latest:
        st.warning(f"No scan found for {computer}.")
        return
    latest = latest[0]

    # Is the scheduled task on that PC still running?
    # If two scheduled runs in a row are missing, call it offline.
    if seconds_since(latest["last_scan"]) < task_hours * 3600 * 2:
        st.caption(f"🟢 Scanner on **{computer}** is running on schedule. "
                   f"Last scan {time_ago(latest['last_scan'])}.")
    else:
        st.warning(f"🔴 Scanner on **{computer}** has missed its scans. "
                   f"Last scan {time_ago(latest['last_scan'])}. Check the scheduled task on that PC.")

    # Most recent change, from the history table
    if len(history) >= 2:
        newest, before = history[0]["software"], history[1]["software"]
        added = [n for n in newest if n not in before]
        removed = [n for n in before if n not in newest]
        when = time_ago(history[0]["scanned_at"])
        if added:
            st.warning(f"🆕 Added {when}: " + ", ".join(added))
        if removed:
            st.info(f"➖ Removed {when}: " + ", ".join(removed))

    show_dashboard(latest["software"], history)


live_view()