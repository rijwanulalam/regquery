"""
software_scanner.py - one program that does everything on a Windows PC.

  Double-click it  ->  a menu: Install, Scan now, or Uninstall
  Install          ->  asks for the Supabase secret key, does a test scan,
                       copies itself to C:\\ProgramData\\SoftwareScanner and
                       adds a Task Scheduler task (9 AM and 9 PM every day)
  --scan           ->  what Task Scheduler runs: one silent scan, then exit

Each scan runs 'hostname' and 'reg query HKLM\\SOFTWARE' and uploads the
results to Supabase under this PC's name. The Streamlit dashboard then
reads them from Supabase.

Uses only the Python standard library, so PyInstaller can turn it into
one .exe file.
"""

import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

# ---------- Settings you can change ----------
SUPABASE_URL = "https://izijlqusvfsqrdrekqie.supabase.co"  # the URL is not secret
SCAN_TIMES = ["09:00", "21:00"]                           # when Task Scheduler runs a scan
TASK_NAME = "Software Scanner"
INSTALL_DIR = r"C:\ProgramData\SoftwareScanner"
EXE_NAME = "SoftwareScanner.exe"

CONFIG_FILE = os.path.join(INSTALL_DIR, "config.json")    # holds this PC's secret key
LOG_FILE = os.path.join(INSTALL_DIR, "scanner_log.txt")


# ============================================================
#  Small helpers
# ============================================================
def is_admin():
    """True if this program is running with administrator rights."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def restart_as_admin():
    """Open this program again with the Windows 'Run as administrator' prompt."""
    if getattr(sys, "frozen", False):
        # Running as the .exe
        program, arguments = sys.executable, " ".join(sys.argv[1:])
    else:
        # Running as a .py file with Python
        program = sys.executable
        arguments = f'"{os.path.abspath(__file__)}" ' + " ".join(sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", program, arguments, None, 1)


def get_machine_name():
    """Run the Windows 'hostname' command to get this PC's name."""
    result = subprocess.run(["hostname"], capture_output=True, text=True)
    return result.stdout.strip() or platform.node()


def write_log(line):
    """Add one line to scanner_log.txt (and show it on screen)."""
    print(line)
    try:
        with open(LOG_FILE, "a") as file:
            file.write(line + "\n")
    except OSError:
        pass  # logging must never stop a scan


# ============================================================
#  Talking to Supabase
# ============================================================
def supabase_request(secret_key, method, path, data=None, extra_headers=None):
    """Send one request to the Supabase REST API and return the reply (or None)."""
    headers = {"apikey": secret_key, "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)

    body = json.dumps(data).encode("utf-8") if data is not None else None
    request = urllib.request.Request(SUPABASE_URL + "/rest/v1/" + path,
                                     data=body, headers=headers, method=method)

    with urllib.request.urlopen(request, timeout=15) as response:
        text = response.read().decode("utf-8")
        return json.loads(text) if text else None


# ============================================================
#  One scan
# ============================================================
def scan_registry():
    """Run reg query and return the software names (part after the last backslash)."""
    result = subprocess.run(["reg", "query", r"HKLM\SOFTWARE"], capture_output=True, text=True)

    names = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("HKEY_LOCAL_MACHINE"):
            names.append(line.split("\\")[-1])
    return names


def run_scan(secret_key):
    """Scan this PC, upload to Supabase, and return a one-line summary."""
    computer = get_machine_name()
    name = urllib.parse.quote(computer)

    # What did Supabase have last time? Used to spot changes.
    rows = supabase_request(secret_key, "GET", f"computers?computer_name=eq.{name}&select=software")
    last_software = rows[0]["software"] if rows else None

    software = scan_registry()
    changed = software != last_software
    now = datetime.now(timezone.utc).isoformat()
    row = {"computer_name": computer, "software": software}

    # Insert this PC's row, or update it if it already exists
    supabase_request(secret_key, "POST", "computers?on_conflict=computer_name",
                     {**row, "last_scan": now}, {"Prefer": "resolution=merge-duplicates"})

    # Only add a history row when something changed
    if changed:
        supabase_request(secret_key, "POST", "scan_history", {**row, "scanned_at": now})

    status = "changed, saved to history" if changed else "no change"
    return f"{computer}: {len(software)} entries sent ({status})"


def error_message(error):
    """Turn a network error into a short, readable message."""
    if isinstance(error, urllib.error.HTTPError):
        detail = error.read().decode(errors="replace")
        if error.code in (401, 403):
            return f"Supabase rejected the key (error {error.code}). Check the secret key. {detail}"
        return f"Supabase error {error.code}: {detail}"
    return f"Could not reach Supabase: {error.reason}"


def load_secret_key():
    """Read this PC's secret key from config.json, or None if not installed."""
    try:
        with open(CONFIG_FILE) as file:
            return json.load(file)["secret_key"]
    except (OSError, KeyError, ValueError):
        return None


# ============================================================
#  Task Scheduler
# ============================================================
def create_task(command, arguments):
    """Create the scheduled task from an XML description, so every setting is exact."""
    today = date.today().isoformat()

    # One daily trigger for each time in SCAN_TIMES
    triggers = ""
    for time_of_day in SCAN_TIMES:
        triggers += f"""
    <CalendarTrigger>
      <StartBoundary>{today}T{time_of_day}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>"""

    task_xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Scans installed software and uploads it to Supabase.</Description>
  </RegistrationInfo>
  <Triggers>{triggers}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{INSTALL_DIR}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""
    # Notes on the settings above:
    #   S-1-5-18                       = the SYSTEM account (runs without anyone logged in)
    #   DisallowStartIfOnBatteries     = false, so laptops on battery still scan
    #   StartWhenAvailable             = true, so a scan missed while the PC was off runs later
    #   ExecutionTimeLimit PT10M       = stop it if a scan ever hangs for 10 minutes

    xml_path = os.path.join(INSTALL_DIR, "task.xml")
    with open(xml_path, "w", encoding="utf-16") as file:
        file.write(task_xml)

    result = subprocess.run(["schtasks", "/create", "/tn", TASK_NAME, "/xml", xml_path, "/f"],
                            capture_output=True, text=True)
    os.remove(xml_path)

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def lock_down_folder():
    """Only SYSTEM and Administrators may open the install folder (it holds the key)."""
    # *S-1-5-18 = SYSTEM, *S-1-5-32-544 = Administrators (works in any Windows language)
    subprocess.run(["icacls", INSTALL_DIR, "/inheritance:r",
                    "/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"],
                   capture_output=True)


# ============================================================
#  Menu actions
# ============================================================
def install():
    print("\nYou need a Supabase secret key for this PC.")
    print("Supabase > Project Settings > API Keys > Secret keys > New secret key\n")
    secret_key = input("Paste the secret key (starts with sb_secret_): ").strip()

    if not secret_key.startswith("sb_secret_"):
        print("\nThat doesn't look like a secret key. Nothing was installed.")
        return

    os.makedirs(INSTALL_DIR, exist_ok=True)

    # 1. Test scan first, so we never install with a wrong key
    print("\nRunning a test scan...")
    try:
        summary = run_scan(secret_key)
    except urllib.error.URLError as error:
        print("\n" + error_message(error))
        print("Nothing was installed.")
        return
    print("  " + summary)

    # 2. Save the key and lock the folder so normal users can't read it
    with open(CONFIG_FILE, "w") as file:
        json.dump({"secret_key": secret_key}, file)
    lock_down_folder()

    # 3. Copy this program into the install folder
    if getattr(sys, "frozen", False):
        installed_exe = os.path.join(INSTALL_DIR, EXE_NAME)
        if os.path.abspath(sys.executable).lower() != installed_exe.lower():
            shutil.copy2(sys.executable, installed_exe)
        command, arguments = installed_exe, "--scan"
    else:
        # Running as a .py file (for testing): Task Scheduler runs Python with this script
        command, arguments = sys.executable, f'"{os.path.abspath(__file__)}" --scan'

    # 4. Create the scheduled task
    try:
        create_task(command, arguments)
    except RuntimeError as error:
        print(f"\nCould not create the scheduled task: {error}")
        return

    write_log(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Installed. {summary}")
    print("\nDone! This PC now scans every day at " + " and ".join(SCAN_TIMES) + ".")
    print(f"Files are in {INSTALL_DIR}. It appears in the dashboard as {get_machine_name()}.")


def scan_now():
    secret_key = load_secret_key()
    if not secret_key:
        print("\nNot installed yet. Choose 1 to install first.")
        return
    try:
        write_log(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {run_scan(secret_key)}")
    except urllib.error.URLError as error:
        write_log(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {error_message(error)}")


def uninstall():
    # Remove the scheduled task
    subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"], capture_output=True)

    # Remove the files. The .exe can't delete itself while running, so it may stay.
    shutil.rmtree(INSTALL_DIR, ignore_errors=True)

    print("\nRemoved the scheduled task and the saved key.")
    if os.path.exists(INSTALL_DIR):
        print(f"You can now delete the folder {INSTALL_DIR} yourself.")
    print("This PC's old scans stay in Supabase. Delete its key there too if you like.")


# ============================================================
#  Start here
# ============================================================
def main():
    # Task Scheduler runs this: one silent scan, then exit
    if "--scan" in sys.argv:
        secret_key = load_secret_key()
        clock = f"[{datetime.now():%Y-%m-%d %H:%M:%S}]"
        if not secret_key:
            write_log(f"{clock} No config.json found. Run the installer again.")
            sys.exit(1)
        try:
            write_log(f"{clock} {run_scan(secret_key)}")
        except urllib.error.URLError as error:
            write_log(f"{clock} {error_message(error)}")
            sys.exit(1)  # non-zero = Task Scheduler shows the run as failed
        return

    # Double-clicked: installing needs administrator rights
    if not is_admin():
        restart_as_admin()
        return

    print("=" * 46)
    print("  Software Scanner")
    print(f"  This PC: {get_machine_name()}")
    print("=" * 46)
    print("  1. Install (or update the key)")
    print("  2. Scan now")
    print("  3. Uninstall")
    print("  4. Exit")
    choice = input("\nChoose 1-4: ").strip()

    if choice == "1":
        install()
    elif choice == "2":
        scan_now()
    elif choice == "3":
        uninstall()

    input("\nPress Enter to close...")


if __name__ == "__main__":
    main()