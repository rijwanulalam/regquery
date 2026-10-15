"""
scanner.py - runs on your Windows PC.

Does ONE scan of HKLM\\SOFTWARE, sends it to Supabase, and exits.
Windows Task Scheduler runs it again at a fixed interval (e.g. every 5 minutes).
  - the "computers" table always gets the newest scan
  - the "scan_history" table gets a new row only when something changed

Uses only the Python standard library. Settings come from config.json.
"""

import json
import os
import platform
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ---------- Settings ----------
# Read config.json from the same folder as this script
script_folder = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(script_folder, "config.json")) as file:
    config = json.load(file)

SUPABASE_URL = config["supabase_url"].rstrip("/")
SECRET_KEY = config["secret_key"]


# ---------- Machine name ----------
def get_machine_name():
    """Run the Windows 'hostname' command to get this PC's name, e.g. RIJWAN-LAPTOP."""
    result = subprocess.run(["hostname"], capture_output=True, text=True)
    name = result.stdout.strip()

    # Fall back to Python's own lookup if the command gave nothing
    if not name:
        name = platform.node()
    return name


# Every scan is saved in Supabase against this name
COMPUTER_NAME = get_machine_name()


# ---------- Talking to Supabase ----------
def supabase_request(method, path, data=None, extra_headers=None):
    """Send one request to the Supabase REST API and return the reply (or None)."""
    url = SUPABASE_URL + "/rest/v1/" + path

    # Supabase keys go in the "apikey" header
    headers = {"apikey": SECRET_KEY, "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)

    body = json.dumps(data).encode("utf-8") if data is not None else None
    request = urllib.request.Request(url, data=body, headers=headers, method=method)

    with urllib.request.urlopen(request, timeout=15) as response:
        text = response.read().decode("utf-8")
        return json.loads(text) if text else None


def get_saved_software():
    """Ask Supabase for this computer's last saved list, so we don't save a duplicate."""
    name = urllib.parse.quote(COMPUTER_NAME)
    rows = supabase_request("GET", f"computers?computer_name=eq.{name}&select=software")
    if rows:
        return rows[0]["software"]
    return None


def save_scan(software, changed):
    """Update the newest scan, and add a history row if the list changed."""
    now = datetime.now(timezone.utc).isoformat()
    row = {"computer_name": COMPUTER_NAME, "software": software}

    # "Upsert": insert this computer's row, or update it if it already exists
    supabase_request(
        "POST",
        "computers?on_conflict=computer_name",
        {**row, "last_scan": now},
        {"Prefer": "resolution=merge-duplicates"},
    )

    if changed:
        supabase_request("POST", "scan_history", {**row, "scanned_at": now})


# ---------- Scanning the registry ----------
def scan_registry():
    """Run reg query, save the text files, and return the software names."""
    result = subprocess.run(["reg", "query", r"HKLM\SOFTWARE"], capture_output=True, text=True)

    with open(os.path.join(script_folder, "regquery.txt"), "w") as file:
        file.write(result.stdout)

    # Read the output line by line and keep the part after the last backslash
    names = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("HKEY_LOCAL_MACHINE"):
            names.append(line.split("\\")[-1])

    with open(os.path.join(script_folder, "softwares.txt"), "w") as file:
        file.write("\n".join(names))

    return names


# ---------- One scan, then exit ----------
def main():
    clock = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        # What did Supabase have last time? Used to spot changes.
        last_software = get_saved_software()

        software = scan_registry()
        changed = software != last_software
        save_scan(software, changed)

        status = "changed, saved to history" if changed else "no change"
        print(f"[{clock}] {COMPUTER_NAME}: {len(software)} entries sent ({status})")

    except urllib.error.HTTPError as error:
        # Supabase answered with an error, e.g. wrong key or missing table
        print(f"[{clock}] {COMPUTER_NAME}: Supabase error {error.code}: {error.read().decode()}")
        sys.exit(1)  # non-zero = Task Scheduler shows the run as failed
    except urllib.error.URLError as error:
        # No internet or wrong URL
        print(f"[{clock}] {COMPUTER_NAME}: Could not reach Supabase: {error.reason}")
        sys.exit(1)


if __name__ == "__main__":
    main()