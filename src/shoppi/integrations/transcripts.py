"""Phone-call transcript sources.

`SignedJsonTranscriptSource` is a working, vendor-neutral source: anything that
can POST JSON with an HMAC signature (a telephony platform's webhook relay, a
Whisper batch job, an automation tool) can deliver transcripts today.

A vendor-specific source is one more subclass of TranscriptSource: implement
`verify_webhook` with the vendor's signature scheme and `parse` to map their
payload onto CallTranscript, then add it to the registry.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime

from shoppi.integrations.base import (
    CallTranscript, CredentialField, Integration, IntegrationError, TranscriptSegment, TranscriptSource,
)

SIGNATURE_HEADER = "x-shoppi-signature"


class SignedJsonTranscriptSource(Integration, TranscriptSource):
    key = "transcript_webhook"
    name = "Call transcripts (signed webhook)"
    description = ("Receive finished phone-call transcripts from your phone system or transcription service. "
                   "Each request must be signed with the shared secret (HMAC-SHA256 of the body, hex, in the "
                   "X-Shoppi-Signature header).")
    category = "telephony"
    connect = "api_key"
    fields = (CredentialField("secret", "Signing secret", secret=True, required=False,
                              help="Leave empty to generate one. You'll paste it into your phone system."),)

    def validate_credentials(self, values: dict[str, str]) -> None:
        if not values.get("secret"):
            values["secret"] = secrets.token_urlsafe(32)
        elif len(values["secret"]) < 16:
            raise IntegrationError("The signing secret must be at least 16 characters.")

    def verify_webhook(self, headers: dict[str, str], body: bytes, creds: dict) -> bool:
        secret = creds.get("secret")
        given = headers.get(SIGNATURE_HEADER, "")
        if not secret or not given:
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, given.removeprefix("sha256="))

    def parse(self, payload: dict) -> CallTranscript:
        return transcript_from_json(payload)


def transcript_from_json(payload: dict) -> CallTranscript:
    """The normalised JSON shape (also accepted by "Add transcript" in the UI)."""
    if not isinstance(payload, dict) or not payload.get("external_id"):
        raise IntegrationError("The transcript needs an external_id.")
    raw = payload.get("segments") or []
    segments = [TranscriptSegment(str(s.get("speaker") or "Speaker"), str(s.get("text") or "").strip(), s.get("start_s"))
                for s in raw if isinstance(s, dict) and str(s.get("text") or "").strip()]
    if not segments and payload.get("text"):
        segments = parse_plain_text(str(payload["text"]))
    if not segments:
        raise IntegrationError("The transcript has no text.")
    started = payload.get("started_at")
    try:
        started_at = datetime.fromisoformat(started.replace("Z", "+00:00")) if isinstance(started, str) else None
    except ValueError as e:
        raise IntegrationError("started_at must be an ISO 8601 time.") from e
    duration = payload.get("duration_s")
    return CallTranscript(
        external_id=str(payload["external_id"])[:200], segments=segments,
        direction="outbound" if payload.get("direction") == "outbound" else "inbound",
        from_number=payload.get("from_number"), to_number=payload.get("to_number"), started_at=started_at,
        duration_s=int(duration) if isinstance(duration, (int, float)) else None,
        language=payload.get("language"), consent_noted=bool(payload.get("consent_noted")),
    )


_SPEAKER = re.compile(r"^\s*([A-Za-z][\w .'-]{0,30}):\s+(.*)$")
_VTT_TIME = re.compile(r"^\d{2}:\d{2}(:\d{2})?\.\d{3}\s+-->")


def parse_plain_text(text: str) -> list[TranscriptSegment]:
    """Pasted text or a .vtt file -> segments. Lines like "Customer: ..." keep their speaker."""
    segments: list[TranscriptSegment] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or line.isdigit() or _VTT_TIME.match(line) or line.startswith("NOTE"):
            continue
        line = re.sub(r"^<v\s+([^>]+)>", r"\1: ", line).replace("</v>", "")
        m = _SPEAKER.match(line)
        if m:
            segments.append(TranscriptSegment(m.group(1).strip(), m.group(2).strip()))
        elif segments:
            segments[-1].text += " " + line
        else:
            segments.append(TranscriptSegment("Speaker", line))
    return segments
