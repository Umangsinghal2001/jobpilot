"""
gmail — read-only access to the candidate's Gmail, for the inbox agent. Never sends, deletes or changes mail: the token's only
permission is gmail.readonly (Google enforces that), and it lives in secrets/ (out of git, owner-only).

    python3 gmail.py --auth     # one-time approval in the browser (again after Google expires it: 7 days in Testing)
    python3 gmail.py            # check the connection: the job label and how many emails it holds

secrets/gmail-client.json : the OAuth desktop client the candidate downloaded from Google Cloud (project JobPilot)
secrets/gmail-token.json  : the approval, written here after --auth
"""
from __future__ import annotations

import sys
from pathlib import Path

SECRETS = Path(__file__).parent / "secrets"
CLIENT, TOKEN = SECRETS / "gmail-client.json", SECRETS / "gmail-token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
LABEL = "Job applications"


def credentials(interactive: bool = False):
    """The saved approval, refreshed if it can be; interactive=True opens the browser to approve again."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:  # noqa: BLE001 — expired for good (Testing mode: 7 days): approve again
            creds = None
    if not (creds and creds.valid):
        if not interactive:
            raise RuntimeError("Gmail isn't connected (or Google expired the approval): run python3 gmail.py --auth")
        from google_auth_oauthlib.flow import InstalledAppFlow
        creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES).run_local_server(
            port=0, open_browser=True, prompt="consent",
            success_message="JobPilot can now read your Gmail (read-only). You can close this tab.")
    SECRETS.mkdir(mode=0o700, exist_ok=True)
    TOKEN.write_text(creds.to_json())
    TOKEN.chmod(0o600)
    return creds


def service(interactive: bool = False):
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=credentials(interactive), cache_discovery=False)


# ── what the inbox agent may see: the candidate's job label, plus Wellfound and LinkedIn job mail, from the job search on ──
# Google can't scope a token to a label, so this is enforced here: every search is narrowed, every read is checked.
SINCE = "2026/09/01"
SENDERS = ("wellfound.com", "jobs-noreply@linkedin.com")
ALLOWED = f'(label:job-applications OR {" OR ".join(f"from:{x}" for x in SENDERS)})'


def search(g, after: str = SINCE, limit: int = 500) -> list[dict]:
    """Ids of the allowed emails after a date (YYYY/MM/DD, never before SINCE), oldest first."""
    after = max(after, SINCE)
    ids, tok = [], None
    while len(ids) < limit:
        r = g.users().messages().list(userId="me", q=f"{ALLOWED} after:{after}", maxResults=min(500, limit),
                                      pageToken=tok).execute()
        ids += [m["id"] for m in r.get("messages", [])]
        if not (tok := r.get("nextPageToken")):
            break
    return list(reversed(ids[:limit]))


def _headers(m: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}


def allowed(g, m: dict, label_id: str) -> bool:
    """In the candidate's job label or from Wellfound / LinkedIn job mail, and not older than SINCE."""
    from datetime import datetime
    sender = _headers(m).get("from", "").lower()
    when = datetime.fromtimestamp(int(m.get("internalDate", 0)) / 1000).strftime("%Y/%m/%d")
    return when >= SINCE and (label_id in m.get("labelIds", []) or any(x in sender for x in SENDERS))


def label_id(g) -> str:
    return next((l["id"] for l in g.users().labels().list(userId="me").execute().get("labels", []) if l["name"] == LABEL), "")


def _text(part: dict) -> str:
    """Plain text of a message: its text/plain parts, else its HTML with the tags stripped."""
    import base64
    import html
    import re
    def walk(p):
        yield p
        for c in p.get("parts", []) or []:
            yield from walk(c)
    parts = list(walk(part))
    def body(p):
        d = p.get("body", {}).get("data")
        return base64.urlsafe_b64decode(d + "=" * (-len(d) % 4)).decode("utf-8", "replace") if d else ""
    plain = "\n".join(body(p) for p in parts if p.get("mimeType") == "text/plain")
    if plain.strip():
        return plain
    h = "\n".join(body(p) for p in parts if p.get("mimeType") == "text/html")
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    return html.unescape(re.sub(r"\s*\n\s*", "\n", re.sub(r"<[^>]+>", "\n", h)))


def read(g, msg_id: str, label: str, full: bool = True) -> dict | None:
    """One allowed email as {id, date, from, subject, snippet, text}; None if it isn't one the candidate allowed."""
    from datetime import datetime
    m = g.users().messages().get(userId="me", id=msg_id, format="full" if full else "metadata",
                                 metadataHeaders=["From", "Subject", "Date"]).execute()
    if not allowed(g, m, label):
        return None
    h = _headers(m)
    return {"id": m["id"], "date": datetime.fromtimestamp(int(m["internalDate"]) / 1000).strftime("%Y-%m-%d %H:%M"),
            "from": h.get("from", ""), "subject": h.get("subject", ""), "snippet": m.get("snippet", ""),
            "text": _text(m.get("payload", {}))[:8000] if full else ""}


if __name__ == "__main__":
    g = service(interactive="--auth" in sys.argv)
    labels = {l["name"]: l["id"] for l in g.users().labels().list(userId="me").execute().get("labels", [])}
    if LABEL in labels:
        n = g.users().labels().get(userId="me", id=labels[LABEL]).execute().get("messagesTotal", 0)
        print(f"Gmail connected (read-only). Label “{LABEL}”: {n} emails.")
    else:
        print(f"Gmail connected (read-only). No label “{LABEL}” yet: create it with the filters.")
