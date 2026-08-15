"""Shared field validation and normalization helpers for auth schemas.

These are used by the Pydantic request models so that the exact same rules
run server-side even when the client-side checks are bypassed (e.g. a direct
API call). Every function raises ``ValueError`` with a field-specific message
that FastAPI surfaces as a 422 ``detail`` entry keyed by the field name.
"""

import re

# --- Constraints ------------------------------------------------------------
USERNAME_MIN = 3
USERNAME_MAX = 30
PASSWORD_MIN = 8
PASSWORD_MAX = 128          # cap to prevent DoS via huge bcrypt payloads
DISPLAY_NAME_MAX = 100
PHONE_MIN_DIGITS = 7        # E.164 subscriber minimum
PHONE_MAX_DIGITS = 15       # E.164 maximum

# --- Patterns ---------------------------------------------------------------
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# RFC 5322-compatible (practical) email pattern. Requires a local part, an "@",
# and a dotted domain with a 2+ char TLD. Rejects strings like
# "asldkfjldsakjfaldkj" that have no "@"/domain.
EMAIL_RE = re.compile(
    r"^(?=.{1,254}$)"
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,}$"
)

# E.164: optional leading "+", country digit 1-9, up to 14 more digits.
E164_RE = re.compile(r"^\+?[1-9]\d{6,14}$")

_TAG_RE = re.compile(r"<[^>]*>")

# Common gTLDs and ccTLDs accepted for email validation. Unknown two-letter
# country codes are also accepted, but nonsense strings like "jhasdlfkj" are
# rejected as invalid top-level domains.
COMMON_EMAIL_TLDS = frozenset({
    "com", "org", "net", "edu", "gov", "mil", "int", "info", "biz", "name",
    "mobi", "jobs", "museum", "travel", "aero", "coop", "pro", "tel", "xxx",
    "app", "dev", "io", "co", "me", "ly", "ai", "cc", "tv", "fm", "am", "at",
    "be", "ca", "ch", "cn", "de", "dk", "es", "eu", "fr", "hk", "ie", "in",
    "it", "jp", "kr", "nl", "nu", "nz", "pl", "ru", "se", "sg", "tw", "uk",
    "us", "br", "au", "mx", "ar", "cl", "za", "ng", "ke", "gh", "ug", "tz",
    "zw", "zm", "et", "eg", "il", "ae", "sa", "qa", "bh", "kw", "om", "jo",
    "iq", "ir", "pk", "bd", "lk", "np", "mm", "th", "vn", "ph", "my", "id",
    "online", "store", "blog", "site", "club", "design", "cloud", "shop", "live",
    "news", "space", "website", "press", "guru", "ninja", "love", "life", "work",
    "world", "global", "group", "team", "agency", "studio", "company",
    "solutions", "services", "support", "systems", "tech", "software", "digital",
    "media", "marketing", "consulting", "business", "city", "network",
    "ventures", "partners", "holdings", "industries", "foundation", "institute",
    "academy", "university", "college", "school", "education", "community",
    "center", "care", "health", "hospital", "clinic", "medical", "pharmacy",
    "fitness", "sport",
})


def validate_username(v: str) -> str:
    """Trim, then enforce length 3-30 and the allowed character set."""
    v = (v or "").strip()
    if len(v) < USERNAME_MIN or len(v) > USERNAME_MAX:
        raise ValueError(
            f"Username must be between {USERNAME_MIN} and {USERNAME_MAX} characters"
        )
    if not USERNAME_RE.match(v):
        raise ValueError(
            "Username may only contain letters, numbers, underscores, and hyphens"
        )
    return v


def normalize_email(v: str) -> str:
    """Trim, validate against the RFC pattern and a sensible TLD list, and
    lowercase for storage."""
    v = (v or "").strip()
    if "@" not in v:
        raise ValueError("Email must contain an @")
    local, at, domain = v.rpartition("@")
    if not local:
        raise ValueError("Missing username before @")
    if not domain:
        raise ValueError("Missing domain after @")
    if "." not in domain:
        raise ValueError("Domain must contain a dot")
    tld = domain.rsplit(".", 1)[1].lower()
    if len(tld) < 2:
        raise ValueError("Domain ending must be at least 2 letters")
    if len(tld) > 6 and tld not in COMMON_EMAIL_TLDS:
        raise ValueError("Enter a valid email domain")
    if tld not in COMMON_EMAIL_TLDS and len(tld) != 2:
        raise ValueError("Enter a valid email domain (e.g. .com, .org, .co.uk)")
    if not EMAIL_RE.match(v):
        raise ValueError("Enter a valid email address")
    return v.lower()


def normalize_phone(v: str) -> str:
    """Parse and canonicalize an international phone number.

    Strips all non-digit characters except an optional leading ``+`` and
    validates the resulting E.164 number (7-15 digits). This keeps domestic
    or international input like ``(555) 987-6543`` or ``+44 20 7946 0958``
    uniform and deterministic.
    """
    raw = (v or "").strip()
    if not raw:
        raise ValueError("Phone number is required")

    # Preserve a leading plus if it is the first character; otherwise keep only digits.
    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError("Enter a valid phone number")

    # A leading plus followed by digits must not start with 0 in the country code.
    if has_plus:
        if digits[0] == "0":
            raise ValueError("Enter a valid phone number in international format")
        normalized = "+" + digits
    else:
        normalized = digits

    if not E164_RE.match(normalized):
        raise ValueError("Enter a valid phone number")

    return normalized


def sanitize_display_name(v):
    """Strip HTML/script tags to prevent stored XSS; cap length; empty -> None."""
    if v is None:
        return None
    v = _TAG_RE.sub("", v).strip()
    if v == "":
        return None
    if len(v) > DISPLAY_NAME_MAX:
        raise ValueError(f"Display name must be at most {DISPLAY_NAME_MAX} characters")
    return v


def validate_password(v: str) -> str:
    """Enforce length 8-128 plus upper/lower/digit complexity."""
    if len(v) < PASSWORD_MIN:
        raise ValueError(f"Password must be at least {PASSWORD_MIN} characters")
    if len(v) > PASSWORD_MAX:
        raise ValueError(f"Password must be at most {PASSWORD_MAX} characters")
    if not re.search(r"[A-Z]", v):
        raise ValueError("Password must contain an uppercase letter")
    if not re.search(r"[a-z]", v):
        raise ValueError("Password must contain a lowercase letter")
    if not re.search(r"\d", v):
        raise ValueError("Password must contain a digit")
    return v
