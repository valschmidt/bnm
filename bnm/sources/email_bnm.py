"""Broadcast Notices to Mariners from a mailbox subscribed to NAVCEN's
GovDelivery BNM emails.

NAVCEN's BNM search page and RSS feeds can't be read by scripts. Its official
distribution channel is email: subscribe a lab mailbox to the BNM topics for
each district at https://www.navcen.uscg.gov (Subscribe to BNM e-mail
updates). Each run then reads the recent messages over IMAP, or from a local
Maildir/mbox, and rebuilds the BNM layer from them. Mail is opened read-only
and is never deleted or marked as read.

Each message becomes one feature:
  - positions are parsed from the text (bnm.parse);
  - the BNM number comes from the subject/body ("BNM 0123-26", "DB BNM 0062-24");
  - "CANCEL AT//241200Z NOV 26" style lines set the end time; otherwise the
    notice is dropped ``bnm_max_age_days`` after it was sent;
  - lines like "CANCEL BNM 0119-26" remove the BNM they name;
  - a later message with the same BNM number replaces an earlier one.
"""

from __future__ import annotations

import email
import email.policy
import html
import imaplib
import logging
import mailbox
import os
import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from ..normalize import make_feature
from ..parse import geometry_from_text
from . import SourceResult

log = logging.getLogger(__name__)

BNM_RE = re.compile(r"\b(?:([A-Z]{2,4})\s+)?BNM\s*(?:NO\.?\s*|#\s*|NR\.?\s*)?(\d{1,4}-\d{2,4})\b", re.I)
DTG_RE = re.compile(
    r"(\d{2})(\d{2})(\d{2})\s*Z\s*([A-Z]{3})\s*(\d{4}|\d{2})\b", re.I)
CANCEL_AT_RE = re.compile(
    r"\bCANCEL(?:LED)?\b(?:\s+(?:THIS|MSG|MESSAGE|BNM|AT|ON|EFFECTIVE|@|/)+)*\s*/*\s*" + DTG_RE.pattern, re.I)
DISTRICT_RE = re.compile(r"\b(?:CGD\s*|D(?:ISTRICT)?\s*)(1|5|7|8|9|11|13|14|17)\b", re.I)
DISTRICT_NAMES = {
    "northeast": 1, "east": 5, "southeast": 7, "heartland": 8, "great lakes": 9,
    "southwest": 11, "northwest": 13, "oceania": 14, "arctic": 17,
}
DISTRICT_NAME_RE = re.compile(
    r"\b(" + "|".join(sorted(DISTRICT_NAMES, key=len, reverse=True)) + r")\s+district\b", re.I)
FOOTER_MARKERS = (
    "you are subscribed to", "update your subscriptions", "this email was sent to",
    "powered by govdelivery", "subscriber services", "manage subscriptions",
)
MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}

NAVCEN_BNM_URL = "https://www.navcen.uscg.gov/broadcast-notice-to-mariners"


# ---------------------------------------------------------------- text helpers

def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


def message_text(msg) -> str:
    """Plain text of a message, without the GovDelivery footer."""
    if not isinstance(msg, EmailMessage):
        msg = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        text = _html_to_text(text)
    text = text.replace("\r\n", "\n").replace("\xa0", " ")
    low = text.lower()
    cut = min((low.find(m) for m in FOOTER_MARKERS if m in low), default=-1)
    if cut > 0:
        text = text[:cut]
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_dtg(day, hh, mm, mon, yy) -> Optional[datetime]:
    try:
        year = int(yy) + (2000 if len(yy) == 2 else 0)
        return datetime(year, MONTHS[mon.upper()], int(day), int(hh), int(mm), tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None


def bnm_numbers(text: str):
    """[(prefix or '', number)] in order of appearance."""
    return [((m.group(1) or "").upper(), m.group(2)) for m in BNM_RE.finditer(text)
            if (m.group(1) or "").upper() not in ("THE", "THIS", "AND", "SEE", "PER", "REF")]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


# ---------------------------------------------------------------- one message

def parse_message(msg, max_age_days: int = 30) -> dict:
    """Parse one BNM email.

    Returns a dict with ``number``, ``cancels`` (numbers it cancels), ``sent``,
    and ``feature`` (None if the text holds no position).
    """
    subject = str(msg.get("Subject", "") or "").strip()
    try:
        sent = parsedate_to_datetime(msg.get("Date"))
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        sent = datetime.now(timezone.utc)
    body = message_text(msg)
    full = f"{subject}\n{body}"

    nums = bnm_numbers(full)
    own = nums[0] if nums else None

    end = None
    cancels = []
    for line in full.splitlines():
        if not re.search(r"\bCANCEL", line, re.I):
            continue
        m = CANCEL_AT_RE.search(line)
        if m:
            end = parse_dtg(*m.groups()[-5:]) or end
        for n in bnm_numbers(line):
            if own is None or n[1] != own[1]:
                cancels.append(n[1])
    if end is None:
        end = sent + timedelta(days=max_age_days)

    district = None
    m = DISTRICT_RE.search(full[:400]) or DISTRICT_NAME_RE.search(full[:400])
    if m:
        g = m.group(1)
        district = int(g) if g.isdigit() else DISTRICT_NAMES[g.lower()]

    number = f"{own[0] + ' ' if own and own[0] else ''}{own[1]}" if own else None
    feature = None
    geom = geometry_from_text(body) or geometry_from_text(subject)
    if geom:
        feature = make_feature(
            geom,
            id=f"bnm:{number or msg.get('Message-ID', subject)}",
            source="BNM",
            category="bnm",
            label="Broadcast Notices to Mariners",
            district=district,
            title=subject or (f"BNM {number}" if number else "Broadcast Notice to Mariners"),
            text=body,
            begin=_iso(sent),
            end=_iso(end),
            updated=_iso(sent),
            bnm=number,
            url=NAVCEN_BNM_URL,
        )
    return {"number": own[1] if own else None, "cancels": cancels, "sent": sent, "feature": feature,
            "subject": subject}


# ---------------------------------------------------------------- mail access

def _password(cfg) -> str:
    pw = os.environ.get("BNM_IMAP_PASSWORD")
    if pw:
        return pw
    if cfg.imap_password_file:
        return Path(cfg.imap_password_file).expanduser().read_text().strip()
    raise RuntimeError("IMAP password not set: use env BNM_IMAP_PASSWORD or imap_password_file")


def _from_ok(msg, wanted: str) -> bool:
    return not wanted or wanted.lower() in str(msg.get("From", "")).lower()


def imap_messages(cfg, since: datetime) -> Iterable[EmailMessage]:
    conn = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port, timeout=cfg.timeout)
    try:
        conn.login(cfg.imap_user, _password(cfg))
        typ, _ = conn.select(cfg.imap_folder, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"cannot open IMAP folder {cfg.imap_folder}")
        criteria = ["SINCE", since.strftime("%d-%b-%Y")]
        if cfg.mail_from:
            criteria += ["FROM", f'"{cfg.mail_from}"']
        typ, data = conn.search(None, *criteria)
        ids = data[0].split() if typ == "OK" and data and data[0] else []
        log.info("IMAP: %d messages since %s", len(ids), since.date())
        for mid in ids:
            typ, parts = conn.fetch(mid, "(BODY.PEEK[])")
            if typ != "OK":
                continue
            for part in parts:
                if isinstance(part, tuple):
                    yield email.message_from_bytes(part[1], policy=email.policy.default)
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def local_messages(path: Path) -> Iterable[EmailMessage]:
    path = Path(path).expanduser()
    if (path / "cur").is_dir() or (path / "new").is_dir():
        box = mailbox.Maildir(str(path), factory=None, create=False)
    else:
        box = mailbox.mbox(str(path), create=False)
    for key in box.iterkeys():
        try:
            raw = box.get_bytes(key)
        except Exception as exc:
            log.warning("cannot read message %s: %s", key, exc)
            continue
        yield email.message_from_bytes(raw, policy=email.policy.default)


# ---------------------------------------------------------------- source

def build_features(messages: Iterable, max_age_days: int, now: datetime,
                   mail_from: str = "") -> Tuple[List[dict], dict]:
    parsed = []
    for msg in messages:
        if not _from_ok(msg, mail_from):
            continue
        try:
            parsed.append(parse_message(msg, max_age_days))
        except Exception as exc:  # one bad message must not sink the run
            log.warning("could not parse message %r: %s", msg.get("Subject"), exc)
    parsed.sort(key=lambda p: p["sent"])

    cutoff = now - timedelta(days=max_age_days * 3)  # cancel times may be far out
    latest, cancelled, unlocated = {}, set(), []
    for p in parsed:
        cancelled.update(p["cancels"])
        if p["sent"] < cutoff:
            continue
        key = p["number"] or (p["feature"] and p["feature"]["properties"]["id"]) or p["subject"]
        if p["feature"] is None:
            unlocated.append(p["subject"])
            latest.pop(key, None)  # a newer, position-less update supersedes the old one
            continue
        latest[key] = p
    feats = [p["feature"] for k, p in latest.items() if not (p["number"] and p["number"] in cancelled)]
    stats = {"messages": len(parsed), "cancelled": len(cancelled), "unlocated": unlocated[-50:]}
    return feats, stats


def fetch(cfg) -> SourceResult:
    result = SourceResult("bnm")
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=cfg.bnm_max_age_days * 3)
    if cfg.mail_path:
        messages = local_messages(cfg.mail_path)
    elif cfg.imap_host:
        messages = imap_messages(cfg, since)
    else:
        raise RuntimeError("BNM source needs imap_host or mail_path in the config")

    feats, stats = build_features(messages, cfg.bnm_max_age_days, now, cfg.mail_from)
    wanted = set(cfg.districts or [])
    result.features = [f for f in feats
                       if f["properties"]["district"] is None or not wanted or f["properties"]["district"] in wanted]
    result.stats = stats
    log.info("BNM: %d messages, %d mapped, %d without position",
             stats["messages"], len(result.features), len(stats["unlocated"]))
    return result
