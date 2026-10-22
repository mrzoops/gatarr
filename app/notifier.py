import requests

def send_approval_notification(token: str, user_key: str, title: str, year: int, reason: str, approve_url: str):
    """
    Sends a Pushover notification containing a 1-tap Approve Download URL.
    """
    payload = {
        "token": token,
        "user": user_key,
        "title": f"Gatarr Alert: {title} ({year})",
        "message": f"Status: {reason}\nAction: Tap below to send to download queue.",
        "url": approve_url,
        "url_title": "Approve Download",
        "priority": 0
    }
    try:
        requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=5)
    except Exception as e:
        print(f"[Notifier Error] Could not send Pushover notification: {e}")
