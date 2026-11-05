"""
Writing and sending the /Column newsletter.

An "issue" is a subject line, a short intro, a handful of published stories
and a sign-off. It is stored in the database (table newsletter_issues) so it
survives Render's redeploys, edited in the admin portal (/admin/newsletter),
and delivered one email per subscriber through Resend's HTTP API.

Settings (Render -> Environment):
  RESEND_API_KEY     required to send anything
  NEWSLETTER_FROM    e.g.  /Column <news@yourdomain.com>. Until you own and
                     verify a domain this stays on Resend's test sender, which
                     only delivers to your own Resend account email.
  NEWSLETTER_REPLY_TO  optional, where replies go
  NEWSLETTER_ADDRESS   postal address printed in the footer (anti-spam laws in
                     the US, EU and elsewhere expect one on marketing email)
  SITE_URL           e.g. https://the-sod-father.onrender.com (links in emails)

Sending is split into chunks (send_chunk) so no single web request runs long
enough for gunicorn to kill it. Every delivery is logged in
newsletter_deliveries, so pressing "send" again only emails people who have
not received this issue yet -- safe after a timeout, an error, or hitting
Resend's daily limit.
"""
import hashlib
import hmac
import json
import os
import re
import time
from urllib.parse import quote

import requests
from flask import current_app, render_template, request

import db
from content import get_article, get_articles

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "/Column <onboarding@resend.dev>"
CHUNK_SECONDS = 18          # stay well under gunicorn's 30s worker timeout
MAX_SUBJECT = 150
MAX_PREVIEW = 200
MAX_TEXT = 6000


# ── Settings ─────────────────────────────────────────────────────────────

def api_key():
    return os.environ.get("RESEND_API_KEY", "").strip()


def from_address():
    return os.environ.get("NEWSLETTER_FROM", "").strip() or DEFAULT_FROM


def is_test_sender():
    return "resend.dev" in from_address().lower()


def postal_address():
    return os.environ.get("NEWSLETTER_ADDRESS", "").strip()


def site_url():
    url = os.environ.get("SITE_URL", "").strip().rstrip("/")
    if url:
        return url
    if request:
        return request.url_root.rstrip("/")
    return "https://the-sod-father.onrender.com"


def absolute(path):
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    return site_url() + (path if path.startswith("/") else "/" + path)


# ── Unsubscribe links ────────────────────────────────────────────────────

def unsub_token(email):
    """A signature only this server can make, so an unsubscribe link can't be
    forged for somebody else's address. Nothing extra to store."""
    key = (current_app.secret_key or "").encode()
    return hmac.new(key, ("newsletter-unsub:" + email.lower()).encode(), hashlib.sha256).hexdigest()[:32]


def token_ok(email, token):
    return bool(email) and hmac.compare_digest(unsub_token(email), token or "")


def unsubscribe_url(email):
    return f"{site_url()}/newsletter/unsubscribe?e={quote(email)}&t={unsub_token(email)}"


# ── Issues (database) ────────────────────────────────────────────────────

def list_issues():
    rows = db.query("SELECT * FROM newsletter_issues ORDER BY updated_at DESC")
    for row in rows:
        row["slugs"] = json.loads(row["slugs"] or "[]")
        row["delivered"] = (db.query_one(
            "SELECT COUNT(*) AS n FROM newsletter_deliveries WHERE issue_id = :i AND status = 'sent'",
            i=row["id"]) or {}).get("n", 0)
    return rows


def get_issue(issue_id):
    row = db.query_one("SELECT * FROM newsletter_issues WHERE id = :i", i=issue_id)
    if row:
        row["slugs"] = json.loads(row["slugs"] or "[]")
    return row


def clean_issue_form(form):
    """Pulls an issue out of the editor form. Returns (issue_dict, errors)."""
    subject = " ".join((form.get("subject") or "").split())[:MAX_SUBJECT]
    preview = " ".join((form.get("preview") or "").split())[:MAX_PREVIEW]
    intro = (form.get("intro") or "").replace("\r\n", "\n").strip()[:MAX_TEXT]
    outro = (form.get("outro") or "").replace("\r\n", "\n").strip()[:MAX_TEXT]
    wanted = form.getlist("slugs") if hasattr(form, "getlist") else list(form.get("slugs") or [])
    known = {a["slug"] for a in get_articles()}
    slugs = [s for s in wanted if s in known]
    errors = []
    if not subject:
        errors.append("Give the issue a subject line.")
    return {"subject": subject, "preview": preview, "intro": intro, "outro": outro, "slugs": slugs}, errors


def save_issue(issue, issue_id=None):
    now = db.now_iso()
    params = dict(subject=issue["subject"] or "(untitled issue)", preview=issue["preview"],
                  intro=issue["intro"], outro=issue["outro"], slugs=json.dumps(issue["slugs"]), t=now)
    if issue_id:
        db.execute(
            "UPDATE newsletter_issues SET subject=:subject, preview=:preview, intro=:intro, "
            "outro=:outro, slugs=:slugs, updated_at=:t WHERE id=:i AND status <> 'sent'",
            i=issue_id, **params)
        return issue_id
    issue_id = db.new_id()
    db.execute(
        "INSERT INTO newsletter_issues (id, subject, preview, intro, outro, slugs, status, created_at, updated_at) "
        "VALUES (:i, :subject, :preview, :intro, :outro, :slugs, 'draft', :t, :t)",
        i=issue_id, **params)
    return issue_id


def delete_issue(issue_id):
    db.execute("DELETE FROM newsletter_deliveries WHERE issue_id = :i", i=issue_id)
    db.execute("DELETE FROM newsletter_issues WHERE id = :i AND status <> 'sent'", i=issue_id)


# ── Building the email ───────────────────────────────────────────────────

def _paragraphs(text):
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def _stories(issue):
    out = []
    for slug in issue["slugs"]:
        a = get_article(slug)
        if not a:
            continue  # unpublished or deleted since the issue was saved
        out.append({
            "title": a["title"],
            "dek": a.get("dek") or "",
            "pill": a.get("pill") or "",
            "author": a.get("author") or "",
            "url": f"{site_url()}/article/{a['slug']}",
            "cover": absolute(a.get("cover_image")),
        })
    return out


def render_issue(issue, email=None, name=""):
    """Returns (html, text) for one recipient (or a generic copy for preview)."""
    unsub = unsubscribe_url(email) if email else f"{site_url()}/newsletter/unsubscribe"
    ctx = {
        "issue": issue,
        "intro": _paragraphs(issue["intro"]),
        "outro": _paragraphs(issue["outro"]),
        "stories": _stories(issue),
        "greeting": ("Hi " + name.split()[0] + ",") if name.strip() else "",
        "unsubscribe_url": unsub,
        "address": postal_address(),
        "site_url": site_url(),
    }
    html = render_template("email/newsletter.html", **ctx)

    lines = []
    if ctx["greeting"]:
        lines += [ctx["greeting"], ""]
    for p in ctx["intro"]:
        lines += [p, ""]
    for st in ctx["stories"]:
        lines += [st["title"].upper()] + ([st["dek"]] if st["dek"] else []) + [st["url"], ""]
    for p in ctx["outro"]:
        lines += [p, ""]
    lines += ["--", "/Column  " + site_url(), f"Unsubscribe: {unsub}"]
    if ctx["address"]:
        lines.append(ctx["address"])
    return html, "\n".join(lines)


# ── Sending ──────────────────────────────────────────────────────────────

class QuotaReached(Exception):
    pass


def _post(payload):
    return requests.post(
        RESEND_URL, json=payload, timeout=15,
        headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"})


def send_email(to, subject, html, text, unsub_url=None):
    """Sends one email. Returns (ok, error_message). Raises QuotaReached when
    Resend says the day's or month's allowance is used up."""
    payload = {"from": from_address(), "to": [to], "subject": subject, "html": html, "text": text}
    reply_to = os.environ.get("NEWSLETTER_REPLY_TO", "").strip()
    if reply_to:
        payload["reply_to"] = reply_to
    if unsub_url:
        payload["headers"] = {
            "List-Unsubscribe": f"<{unsub_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
    for attempt in (1, 2):
        try:
            resp = _post(payload)
        except requests.RequestException as exc:
            return False, f"Couldn't reach Resend: {exc}"
        if resp.status_code < 300:
            return True, None
        try:
            body = resp.json()
        except ValueError:
            body = {}
        message = body.get("message") or resp.text[:200] or f"HTTP {resp.status_code}"
        name = str(body.get("name") or "")
        if resp.status_code == 429:
            if "quota" in name or "quota" in message.lower():
                raise QuotaReached(message)
            if attempt == 1:  # plain rate limit: wait a moment, try once more
                time.sleep(min(float(resp.headers.get("retry-after", 1) or 1), 3))
                continue
        return False, f"{resp.status_code}: {message}"
    return False, "Rate limited by Resend"


def subscribers(active_only=True):
    sql = ("SELECT s.email, s.name FROM newsletter_subscribers s "
           "LEFT JOIN newsletter_unsubscribes u ON u.email = s.email ")
    if active_only:
        sql += "WHERE u.email IS NULL "
    return db.query(sql + "ORDER BY s.created_at")


def progress(issue_id, retry_failed=False):
    """Returns (delivered, failed, todo): how many got this issue, how many
    attempts failed, and who is still waiting. Failed addresses (a typo, a
    mailbox that doesn't exist) are only tried again when retry_failed is set,
    so one bad address can't keep an issue from finishing."""
    recipients = subscribers()
    log = {r["email"]: r["status"] for r in db.query(
        "SELECT email, status FROM newsletter_deliveries WHERE issue_id = :i", i=issue_id)}
    delivered = sum(1 for r in recipients if log.get(r["email"]) == "sent")
    failed = sum(1 for r in recipients if log.get(r["email"]) == "failed")
    skip = {"sent"} if retry_failed else {"sent", "failed"}
    todo = [r for r in recipients if log.get(r["email"]) not in skip]
    return delivered, failed, todo


def send_chunk(issue, retry_failed=False):
    """Emails as many not-yet-served subscribers as fit in about CHUNK_SECONDS.
    Returns a dict the editor page uses to show progress."""
    issue_id = issue["id"]
    _, _, todo = progress(issue_id, retry_failed)
    started = time.time()
    sent_now = failed_now = 0
    last_error = None
    quota = False

    for person in todo:
        if time.time() - started > CHUNK_SECONDS:
            break
        email = person["email"]
        html, text = render_issue(issue, email=email, name=person.get("name") or "")
        try:
            ok, err = send_email(email, issue["subject"], html, text, unsub_url=unsubscribe_url(email))
        except QuotaReached as exc:
            quota, last_error = True, str(exc)
            break
        db.execute("DELETE FROM newsletter_deliveries WHERE issue_id = :i AND email = :e", i=issue_id, e=email)
        db.execute(
            "INSERT INTO newsletter_deliveries (issue_id, email, status, error, created_at) "
            "VALUES (:i, :e, :s, :err, :t)",
            i=issue_id, e=email, s="sent" if ok else "failed", err=err, t=db.now_iso())
        if ok:
            sent_now += 1
        else:
            failed_now += 1
            last_error = err
        time.sleep(0.12)  # stay under Resend's 10 requests/second

    delivered, failed, remaining = progress(issue_id)
    finished = not remaining and not quota
    if finished and delivered:
        db.execute("UPDATE newsletter_issues SET status='sent', sent_at=:t, updated_at=:t WHERE id=:i",
                   i=issue_id, t=db.now_iso())
    last_failure = db.query_one(
        "SELECT error FROM newsletter_deliveries WHERE issue_id = :i AND status = 'failed' "
        "ORDER BY created_at DESC LIMIT 1", i=issue_id)
    return {
        "sent_now": sent_now, "failed_now": failed_now, "delivered": delivered,
        "failed": failed, "remaining": len(remaining), "finished": finished,
        "quota": quota, "error": last_error or (last_failure or {}).get("error"),
    }
