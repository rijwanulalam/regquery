'''import subprocess

# Step 1: Run "reg query HKLM\SOFTWARE" and get its output as text
result = subprocess.run(["reg", "query", r"HKLM\SOFTWARE"], capture_output=True, text=True)

# Step 2: Save the output to regquery.txt
with open("regquery.txt", "w") as file:
    file.write(result.stdout)

# Step 3: Read regquery.txt line by line and write the software names to softwares.txt
with open("regquery.txt", "r") as infile, open("softwares.txt", "w") as outfile:
    for line in infile:
        line = line.strip()

        # Only keep lines that are registry keys, skip blank lines
        if line.startswith("HKEY_LOCAL_MACHINE"):
            # The software name is the part after the last backslash
            software = line.split("\\")[-1]
            outfile.write(software + "\n")

print("Done! Check softwares.txt")
'''

import platform
import subprocess
from datetime import datetime
import streamlit as st

# ---------- Page setup ----------
st.set_page_config(page_title="Software Scanner", page_icon="🛡️", layout="wide")
st.title("🛡️ Software Scanner")
st.write("Live view of software in `HKLM\\SOFTWARE`, with blacklisted ones flagged.")

# The registry only exists on Windows. On Streamlit Cloud (Linux)
# you upload a regquery.txt file instead.
on_windows = platform.system() == "Windows"


# ---------- Helper functions ----------
def get_names(text):
    """Go through the reg query output line by line and return the software names."""
    names = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("HKEY_LOCAL_MACHINE"):
            names.append(line.split("\\")[-1])  # part after the last backslash
    return names


def scan_registry():
    """Run reg query on this computer, save it to regquery.txt, and return the names."""
    result = subprocess.run(["reg", "query", r"HKLM\SOFTWARE"], capture_output=True, text=True)
    with open("regquery.txt", "w") as file:
        file.write(result.stdout)
    return get_names(result.stdout)


def read_uploaded_file(uploaded_file):
    """Turn an uploaded file into text. Handles both UTF-16 and UTF-8 files."""
    data = uploaded_file.getvalue()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # PowerShell '>' saves UTF-16
        return data.decode("utf-16")
    return data.decode("utf-8-sig", errors="replace")


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


# ---------- The dashboard (numbers, charts, table) ----------
def show_dashboard(software):
    blacklisted = [name for name in software if is_blacklisted(name)]
    clean_count = len(software) - len(blacklisted)

    # Save both lists to text files, like the original script
    if on_windows:
        with open("softwares.txt", "w") as file:
            file.write("\n".join(software))
        with open("blacklist_softwares.txt", "w") as file:
            file.write("\n".join(blacklisted))

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


# ---------- On Windows: live, auto-refreshing scan ----------
if on_windows:
    st.sidebar.header("Live refresh")
    auto_refresh = st.sidebar.toggle("Auto refresh", value=True)
    seconds = st.sidebar.number_input("Refresh every (seconds)", min_value=5, value=10, step=5)

    # None means "don't refresh automatically"
    refresh_every = seconds if auto_refresh else None

    # A fragment is a part of the page that can rerun on its own.
    # run_every makes Streamlit rerun it on a timer, so the scan stays live.
    @st.fragment(run_every=refresh_every)
    def live_view():
        software = scan_registry()

        # Compare with the previous scan to spot changes
        previous = st.session_state.get("previous_scan")
        st.session_state["previous_scan"] = software

        st.caption(f"🟢 Last scanned at {datetime.now():%H:%M:%S}")

        if previous is not None:
            added = [name for name in software if name not in previous]
            removed = [name for name in previous if name not in software]
            if added:
                st.warning("🆕 New since last scan: " + ", ".join(added))
            if removed:
                st.info("➖ Removed since last scan: " + ", ".join(removed))

        show_dashboard(software)

    live_view()

# ---------- Anywhere else (e.g. Streamlit Cloud): upload a file ----------
else:
    uploaded = st.sidebar.file_uploader("Upload regquery.txt", type="txt")
    if uploaded is None:
        st.info("Upload a **regquery.txt** file in the sidebar to start.")
        st.write("To create the file on a Windows PC:")
        st.markdown(
            "1. Press the **Windows key** and type **cmd**\n"
            "2. Right-click **Command Prompt** and choose **Run as administrator**\n"
            "3. Click **Yes** when Windows asks for permission\n"
            "4. Run this command:"
        )
        st.code(r"reg query HKLM\SOFTWARE > %USERPROFILE%\Desktop\regquery.txt")
        st.write("5. The file **regquery.txt** appears on your Desktop. Upload it in the sidebar.")
    else:
        show_dashboard(get_names(read_uploaded_file(uploaded)))