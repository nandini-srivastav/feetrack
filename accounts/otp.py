"""Phone + one-time-code sign-in for parents.

Only an HMAC of the code is stored. Codes expire after 5 minutes, allow 5
attempts, and a phone can request at most 3 codes per 15 minutes. The
same message is shown whether or not the number is registered, so the
form cannot be used to find out who is a parent here.
"""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from accounts.models import OTPChallenge, User
from reminders.models import OutboundMessage

CODE_TTL = timedelta(minutes=5)
WINDOW = timedelta(minutes=15)
MAX_PER_WINDOW = 3


class OTPError(Exception):
    pass


def normalise_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


def _hash(phone, code):
    return hmac.new(settings.SECRET_KEY.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def request_code(phone: str) -> str | None:
    """Create and 'send' a code. Returns the code (for demo display) or None
    if the number is not a parent. Raises OTPError when rate-limited."""
    recent = OTPChallenge.objects.filter(phone=phone, created_at__gte=timezone.now() - WINDOW).count()
    if recent >= MAX_PER_WINDOW:
        raise OTPError("Too many codes requested. Please wait 15 minutes and try again.")
    user = User.objects.filter(phone=phone, role=User.Role.PARENT, is_active=True).first()
    if user is None:
        return None
    code = f"{secrets.randbelow(10**6):06d}"
    OTPChallenge.objects.create(phone=phone, code_hash=_hash(phone, code), expires_at=timezone.now() + CODE_TTL)
    body = (f"आपका FeeTrack कोड {code} है। यह 5 मिनट तक मान्य है।" if user.preferred_language == "hi"
            else f"Your FeeTrack code is {code}. It is valid for 5 minutes.")
    OutboundMessage.objects.create(to_phone=phone, language=user.preferred_language, kind=OutboundMessage.Kind.OTP, body=body)
    return code


def verify(phone: str, code: str) -> User:
    challenge = OTPChallenge.objects.filter(phone=phone, used=False).order_by("-created_at").first()
    if challenge is None or challenge.expires_at < timezone.now():
        raise OTPError("This code has expired. Ask for a new one.")
    if challenge.attempts >= OTPChallenge.MAX_ATTEMPTS:
        raise OTPError("Too many wrong tries. Ask for a new code.")
    challenge.attempts += 1
    challenge.save(update_fields=["attempts"])
    if not hmac.compare_digest(challenge.code_hash, _hash(phone, (code or "").strip())):
        left = OTPChallenge.MAX_ATTEMPTS - challenge.attempts
        raise OTPError(f"That code is not right. {left} tr{'y' if left == 1 else 'ies'} left.")
    challenge.used = True
    challenge.save(update_fields=["used"])
    return User.objects.get(phone=phone, role=User.Role.PARENT)
