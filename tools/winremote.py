#!/usr/bin/env python3
"""Work on the Windows side of MX Options from a Linux machine, over SSH.

The Windows machine needs an OpenSSH server, Python with PySide6, and to be signed in to (its
desktop is where windows show and where keys can be sent). Name it with --host or MXOPTIONS_WINHOST,
as an alias of your ~/.ssh/config. Its copy of the source tree is the folder "mxoptions" in the
home folder of the SSH user.

    tools/winremote.py push                     copy this source tree there
    tools/winremote.py test                     run the unit tests there
    tools/winremote.py py script.py [args]      run a script there, in the SSH session (no desktop: HID works, windows do not)
    tools/winremote.py gui script.py [args]     run a script on the desktop there and print what it printed
    tools/winremote.py app [stop|built|installed]   start MX Options on the desktop there (from the tree, as built, as installed), or stop it
    tools/winremote.py log [lines]              the end of its log
    tools/winremote.py call Name [args]         ask the MX Options running there (mxoptions --call)

A script run with "gui" sends keys and clicks to whatever has the keyboard on that desktop. Make it
check that its own window has it (GetForegroundWindow) before it sends anything."""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TREE = "mxoptions"
PYTHON = r"%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
BOOT = '''"""Runs a script on the desktop of the signed-in user (started by a scheduled task: tools/winremote.py gui).
What the script prints goes to _gui_out.txt, since there is no console here."""
import json, os, runpy, sys, traceback
here = os.path.dirname(os.path.abspath(__file__))
os.chdir(here)
out = open(os.path.join(here, "_gui_out.txt"), "w", buffering=1, encoding="utf-8")
sys.stdout = sys.stderr = out
status = 0
try:
    job = json.load(open(os.path.join(here, "_gui_job.json"), encoding="utf-8"))
    sys.argv = [job["script"]] + job.get("args", [])
    sys.path.insert(0, here)
    runpy.run_path(os.path.join(here, job["script"]), run_name="__main__")
except SystemExit as stop:
    status = stop.code or 0
except BaseException:
    traceback.print_exc()
    status = 1
print(f"[finished {status}]")
out.close()
'''
TASK = r'''
$tree = "$HOME\mxoptions"
$pyw = [Environment]::ExpandEnvironmentVariables("{python}").Replace("python.exe", "pythonw.exe")
$program = "{program}"
if (-not $program) {{ $program = $pyw }}
$action = New-ScheduledTaskAction -Execute $program -Argument "{arguments}" -WorkingDirectory $tree
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 0)
Register-ScheduledTask -TaskName "{task}" -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "{task}"
'''


def ssh(host, command, **more):
    return subprocess.run(["ssh", "-o", "ConnectTimeout=10", host, command], **more)


def powershell(host, script, **more):
    """Run PowerShell there. Sent encoded: the shell on the way (cmd.exe) would read things into its quotes."""
    script = "$ProgressPreference = 'SilentlyContinue'; [Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ssh(host, f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}", **more)


def python_there(host, arguments, **more):
    return ssh(host, f'cd {TREE} & set PYTHONUTF8=1& set PYTHONIOENCODING=utf-8& "{PYTHON}" {arguments}', **more)


def push(host):
    packed = subprocess.Popen(["tar", "-C", ROOT, "-cf", "-", "--exclude=.git", "--exclude=dist", "--exclude=build",
                               "--exclude=__pycache__", "--exclude=docs/screenshots", "--exclude=*.pyc", "."], stdout=subprocess.PIPE)
    done = ssh(host, f"(if not exist {TREE} mkdir {TREE}) & tar -xf - -C {TREE}", stdin=packed.stdout)
    packed.wait()
    return done.returncode or packed.returncode


def copy(host, path, name):
    return subprocess.run(["scp", "-q", path, f"{host}:{TREE}/{name}"]).returncode


def gui(host, script, arguments, wait):
    name = "_try_" + os.path.basename(script)
    if copy(host, script, name):
        return 1
    for text, as_name in ((BOOT, "_gui_boot.py"), (json.dumps({"script": name, "args": arguments}), "_gui_job.json")):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8") as made:
            made.write(text)
            made.flush()
            if copy(host, made.name, as_name):
                return 1
    task = TASK.format(python=PYTHON, program="", arguments="_gui_boot.py", task="mxoptions-dev")
    watch = rf'''
Remove-Item "$HOME\{TREE}\_gui_out.txt" -ErrorAction SilentlyContinue
{task}
$deadline = (Get-Date).AddSeconds({wait})
while ((Get-Date) -lt $deadline) {{
    Start-Sleep -Milliseconds 500
    if ((Test-Path "$tree\_gui_out.txt") -and (Select-String -Path "$tree\_gui_out.txt" -Pattern "^\[finished" -Quiet)) {{ break }}
}}
if (Test-Path "$tree\_gui_out.txt") {{ Get-Content "$tree\_gui_out.txt" -Encoding UTF8 }} else {{ "(the script wrote nothing: is anybody signed in there?)" }}
'''
    return powershell(host, watch).returncode


def app(host, what):
    """Start MX Options on the desktop there: from the tree, the program tools/build_windows.py made ("built"), or
    that program as its installer put it ("installed")."""
    script = r'''Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq "pythonw.exe" -and $_.CommandLine -match "main.py") -or $_.Name -eq "mxoptions.exe" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
'''
    if what == "stop":
        script += '"stopped"'
    else:
        program = {"built": r"$HOME\mxoptions\dist\mxoptions\mxoptions.exe",
                   "installed": r"$env:LOCALAPPDATA\Programs\MX Options\mxoptions.exe"}.get(what, "")
        script += TASK.format(python=PYTHON, program=program, arguments="--background" if program else "main.py --background",
                              task="mxoptions-dev-app") + r'''
Start-Sleep -Seconds 7
"running: " + ((Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq "pythonw.exe" -and $_.CommandLine -match "main.py") -or $_.Name -eq "mxoptions.exe" } | ForEach-Object { $_.Name }) -join ", ")'''
    return powershell(host, script).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default=os.environ.get("MXOPTIONS_WINHOST"), help="the SSH alias of the Windows machine")
    parser.add_argument("--wait", type=int, default=40, help="gui: seconds to wait for the script to finish")
    parser.add_argument("what", choices=("push", "test", "py", "gui", "app", "log", "call"))
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.host:
        parser.error("name the Windows machine with --host or MXOPTIONS_WINHOST")
    host, rest = args.host, args.rest
    if args.what == "push":
        return push(host)
    if args.what == "test":
        return ssh(host, f'cd {TREE} & set PYTHONUTF8=1& set QT_QPA_PLATFORM=offscreen& "{PYTHON}" -m unittest discover -s tests').returncode
    if args.what == "py":
        name = "_try_" + os.path.basename(rest[0])
        return copy(host, rest[0], name) or python_there(host, " ".join([name] + rest[1:])).returncode
    if args.what == "gui":
        return gui(host, rest[0], rest[1:], args.wait)
    if args.what == "app":
        return app(host, rest[0] if rest else "")
    if args.what == "log":
        lines = rest[0] if rest else "30"
        return powershell(host, rf'Get-Content -Tail {int(lines)} -Encoding UTF8 "$env:LOCALAPPDATA\mxoptions\mxoptions.log"').returncode
    return python_there(host, "main.py --call " + " ".join(rest), stderr=subprocess.DEVNULL).returncode


if __name__ == "__main__":
    sys.exit(main())
