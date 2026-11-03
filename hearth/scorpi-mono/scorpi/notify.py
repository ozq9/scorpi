"""Notifications: in-app (all devices), host desktop toast, and ntfy push."""

import json
import subprocess
import threading
import time
import urllib.request

from . import db, events
from .config import IS_WINDOWS, SETTINGS


def send(title, body="", level="info", link=""):
    nid = db.x(
        "INSERT INTO notifications (title, body, level, created, link) VALUES (?,?,?,?,?)",
        (title, body, level, time.time(), link),
    )
    events.publish("notification", {"id": nid, "title": title, "body": body, "level": level, "link": link})
    channels = ["app"]
    if SETTINGS["desktop_notifications"]:
        threading.Thread(target=_desktop, args=(title, body), daemon=True).start()
        channels.append("desktop")
    if SETTINGS["ntfy_topic"]:
        threading.Thread(target=_ntfy, args=(title, body, level), daemon=True).start()
        channels.append("ntfy")
    return channels


def _desktop(title, body):
    try:
        if IS_WINDOWS:
            _windows_toast(title, body)
        else:
            subprocess.run(["notify-send", title, body], timeout=5, capture_output=True)
    except Exception:
        pass


def _ps_escape(text):
    return str(text).replace("'", "''")[:400]


def _windows_toast(title, body):
    # Native Windows 10/11 toast through PowerShell; no extra packages needed.
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$n = $t.GetElementsByTagName('text');"
        "$n.Item(0).AppendChild($t.CreateTextNode('%s')) > $null;"
        "$n.Item(1).AppendChild($t.CreateTextNode('%s')) > $null;"
        "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t));"
    ) % (_ps_escape(title), _ps_escape(body))
    subprocess.run(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
        timeout=15, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _ntfy(title, body, level):
    server = (SETTINGS["ntfy_server"] or "https://ntfy.sh").rstrip("/")
    priority = {"urgent": 5, "warning": 4}.get(level, 3)
    payload = {"topic": SETTINGS["ntfy_topic"], "title": title, "message": body or title, "priority": priority, "tags": ["fire"]}
    req = urllib.request.Request(
        server, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception:
        pass
