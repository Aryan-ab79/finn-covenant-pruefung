"""IMAP-Eingang und SMTP-Antwort (getestet mit Gmail + App-Passwort)."""
from __future__ import annotations

import email
import imaplib
import json
import logging
import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import default
from email.utils import formataddr, make_msgid, parseaddr
from pathlib import Path

log = logging.getLogger("finn")

VERARBEITET = Path(os.getenv("FINN_CACHE_DIR", Path(__file__).parent.parent / "cache")) / "verarbeitet.json"
IGNORIEREN = re.compile(r"(no-?reply|mailer-daemon|postmaster)", re.I)


@dataclass
class Auftrag:
    uid: bytes
    message_id: str
    absender: str          # Antwortadresse
    absender_name: str
    betreff: str
    referenzen: str
    anhang_name: str | None
    anhang: bytes | None
    text: str


def _konfig() -> dict:
    fehlend = [k for k in ("FINN_EMAIL", "FINN_EMAIL_PASSWORT") if not os.getenv(k)]
    if fehlend:
        raise SystemExit(f"Fehlende Einstellungen in .env: {', '.join(fehlend)}")
    return {
        "adresse": os.environ["FINN_EMAIL"],
        "passwort": os.environ["FINN_EMAIL_PASSWORT"].replace(" ", ""),
        "imap": os.getenv("FINN_IMAP_HOST", "imap.gmail.com"),
        "smtp": os.getenv("FINN_SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.getenv("FINN_SMTP_PORT", "465")),
    }


def _verarbeitet() -> set[str]:
    return set(json.loads(VERARBEITET.read_text())) if VERARBEITET.exists() else set()


def merke_verarbeitet(message_id: str) -> None:
    ids = _verarbeitet() | {message_id}
    VERARBEITET.parent.mkdir(parents=True, exist_ok=True)
    VERARBEITET.write_text(json.dumps(sorted(ids)))


def lies_auftrag(roh: bytes, uid: bytes = b"0") -> tuple[Auftrag, email.message.EmailMessage]:
    msg = email.message_from_bytes(roh, policy=default)
    name, adresse = parseaddr(str(msg.get("Reply-To") or msg.get("From", "")))
    anhang_name = anhang = None
    for teil in msg.iter_attachments():
        fn = teil.get_filename() or ""
        if fn.lower().endswith((".xlsx", ".xlsm")):
            anhang_name, anhang = fn, teil.get_payload(decode=True)
            break
    body = msg.get_body(preferencelist=("plain", "html"))
    return Auftrag(
        uid=uid,
        message_id=str(msg.get("Message-ID", "")).strip() or f"<ohne-id-{uid.decode()}>",
        absender=adresse,
        absender_name=name,
        betreff=str(msg.get("Subject", "")),
        referenzen=str(msg.get("References", "")),
        anhang_name=anhang_name,
        anhang=anhang,
        text=body.get_content() if body else "",
    ), msg


def hole_auftraege() -> list[Auftrag]:
    k = _konfig()
    erledigt = _verarbeitet()
    neu = []
    with imaplib.IMAP4_SSL(k["imap"]) as imap:
        imap.login(k["adresse"], k["passwort"])
        imap.select("INBOX")
        _, daten = imap.uid("search", None, "UNSEEN")
        for uid in daten[0].split():
            _, teile = imap.uid("fetch", uid, "(BODY.PEEK[])")   # PEEK: erst nach Verarbeitung als gelesen markieren
            roh = next((t[1] for t in teile if isinstance(t, tuple)), None)
            if roh is None:
                continue
            auftrag, msg = lies_auftrag(roh, uid)
            if (auftrag.message_id in erledigt or auftrag.absender.lower() == k["adresse"].lower()
                    or IGNORIEREN.search(auftrag.absender)
                    or str(msg.get("Auto-Submitted", "no")).lower() != "no"):
                imap.uid("store", uid, "+FLAGS", "(\\Seen)")
                continue
            neu.append(auftrag)
    return neu


def markiere_gelesen(uid: bytes) -> None:
    k = _konfig()
    with imaplib.IMAP4_SSL(k["imap"]) as imap:
        imap.login(k["adresse"], k["passwort"])
        imap.select("INBOX")
        imap.uid("store", uid, "+FLAGS", "(\\Seen)")


def baue_antwort(auftrag: Auftrag, text: str, html: str | None = None,
                 anhaenge: list[tuple[str, bytes, str]] | None = None, absender: str | None = None) -> EmailMessage:
    m = EmailMessage()
    adresse = absender or os.getenv("FINN_EMAIL", "finn@localhost")
    m["From"] = formataddr(("Finn – Referent Finanzierung", adresse))
    m["To"] = formataddr((auftrag.absender_name, auftrag.absender))
    betreff = auftrag.betreff or "Covenant-Prüfung"
    m["Subject"] = betreff if betreff.lower().startswith(("re:", "aw:")) else f"Re: {betreff}"
    m["Message-ID"] = make_msgid(domain=adresse.split("@")[-1])
    m["In-Reply-To"] = auftrag.message_id
    m["References"] = f"{auftrag.referenzen} {auftrag.message_id}".strip()
    m["Auto-Submitted"] = "auto-replied"   # verhindert Mail-Schleifen mit anderen Bots
    m.set_content(text)
    if html:
        m.add_alternative(f"<html><body>{html}</body></html>", subtype="html")
    for name, daten, mime in anhaenge or []:
        haupt, sub = mime.split("/")
        m.add_attachment(daten, maintype=haupt, subtype=sub, filename=name)
    return m


def sende(m: EmailMessage) -> None:
    k = _konfig()
    with smtplib.SMTP_SSL(k["smtp"], k["smtp_port"]) as smtp:
        smtp.login(k["adresse"], k["passwort"])
        smtp.send_message(m)
