"""Microsoft Graph mail: sends and reads ONLY the one marketing mailbox (locked by Exchange access policy)."""
import time
import requests
import msal
from config import settings

GRAPH = "https://graph.microsoft.com/v1.0"
_app = None
_tok = {"value": None, "exp": 0}


def _token():
    global _app
    if _tok["value"] and time.time() < _tok["exp"] - 120:
        return _tok["value"]
    if _app is None:
        _app = msal.ConfidentialClientApplication(
            settings.client_id,
            authority=f"https://login.microsoftonline.com/{settings.tenant_id}",
            client_credential=settings.client_secret,
        )
    r = _app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in r:
        raise RuntimeError("Graph sign-in failed: %s" % r.get("error_description", r)[:300])
    _tok["value"] = r["access_token"]
    _tok["exp"] = time.time() + int(r.get("expires_in", 3000))
    return _tok["value"]


def _h():
    return {"Authorization": "Bearer " + _token(), "Content-Type": "application/json"}


def check_connection():
    """Reads 1 message header from the marketing mailbox. Sends nothing."""
    r = requests.get(f"{GRAPH}/users/{settings.mailbox}/mailFolders/inbox/messages?$top=1&$select=subject",
                     headers=_h(), timeout=30)
    return r.status_code, (r.text[:300] if r.status_code != 200 else "ok")


def send_mail(to_email, subject, html_body, reply_to=None):
    msg = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [{"emailAddress": {"address": to_email}}],
    }
    if reply_to:
        msg["replyTo"] = [{"emailAddress": {"address": reply_to}}]
    r = requests.post(f"{GRAPH}/users/{settings.mailbox}/sendMail", headers=_h(),
                      json={"message": msg, "saveToSentItems": True}, timeout=60)
    if r.status_code != 202:
        raise RuntimeError(f"sendMail {r.status_code}: {r.text[:300]}")
    return True


def list_inbox(since_iso, top=100):
    """Messages received since the given ISO time (UTC). Returns a list of dicts."""
    url = (f"{GRAPH}/users/{settings.mailbox}/mailFolders/inbox/messages"
           f"?$top={top}&$orderby=receivedDateTime desc"
           f"&$filter=receivedDateTime ge {since_iso}"
           f"&$select=id,subject,from,receivedDateTime,bodyPreview,body")
    out = []
    while url and len(out) < 500:
        r = requests.get(url, headers=_h(), timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"inbox {r.status_code}: {r.text[:300]}")
        j = r.json()
        out.extend(j.get("value", []))
        url = j.get("@odata.nextLink")
    return out
