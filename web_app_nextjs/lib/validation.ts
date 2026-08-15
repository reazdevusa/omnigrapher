// Client-side validation helpers for the auth forms. These mirror the
// server-side rules in `knowledge_base_pilot/app/validators.py`. Client
// validation is for UX only — the server re-validates everything.

export const USERNAME_MIN = 3;
export const USERNAME_MAX = 30;
export const PASSWORD_MIN = 8;
export const PASSWORD_MAX = 128;
export const DISPLAY_NAME_MAX = 100;
export const PHONE_MIN_DIGITS = 7;
export const PHONE_MAX_DIGITS = 15;

export const USERNAME_RE = /^[A-Za-z0-9_-]+$/;

// RFC 5322-compatible (practical) email pattern — requires a real local part,
// "@", and a dotted domain with a 2+ char TLD. Rejects "asldkfjldsakjfaldkj".
export const EMAIL_RE =
  /^(?=.{1,254}$)[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$/;

export const E164_RE = /^\+?[1-9]\d{6,14}$/;

import {
  AsYouType,
  CountryCode,
  getCountries,
  getCountryCallingCode,
  isValidPhoneNumber,
  parsePhoneNumberFromString,
} from "libphonenumber-js";

// Common gTLDs and ccTLDs accepted on the client. Unknown 2-letter country codes
// are also accepted, but nonsense strings like "jhasdlfkj" are rejected.
export const COMMON_EMAIL_TLDS = new Set([
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
  "world", "global", "group", "team", "agency", "studio", "company", "solutions",
  "services", "support", "systems", "tech", "software", "digital", "media",
  "marketing", "consulting", "business", "city", "network", "ventures",
  "partners", "holdings", "industries", "foundation", "institute", "academy",
  "university", "college", "school", "education", "community", "center", "care",
  "health", "hospital", "clinic", "medical", "pharmacy", "fitness", "sport"
]);

export type PhoneCountry = {
  code: string;
  label: string;
  name: string;
  prefix: string;
  placeholder: string;
};

const countryNames = (() => {
  try {
    return new Intl.DisplayNames(["en"], { type: "region" });
  } catch {
    return { of: (code: string) => code };
  }
})();

export const PHONE_COUNTRIES: PhoneCountry[] = getCountries()
  .map((code) => {
    const calling = getCountryCallingCode(code);
    const name = countryNames.of(code) ?? code;
    return {
      code,
      label: `+${calling}`,
      name,
      prefix: `+${calling}`,
      placeholder: `+${calling}`,
    };
  })
  .sort((a, b) => a.name.localeCompare(b.name));

export const DEFAULT_PHONE_COUNTRY = "US";

export function getPhoneCountry(code: string): PhoneCountry | undefined {
  return PHONE_COUNTRIES.find((c) => c.code === code);
}

export function formatPhoneNumber(value: string, countryCode: string): string {
  if (!value) return value;
  try {
    const cc = countryCode as CountryCode;
    const callingCode = getCountryCallingCode(cc);
    const digits = value.replace(/\D/g, "");
    // Strip any domestic trunk prefix (e.g. Bangladeshi leading 0)
    // and any already-entered country calling code.
    const clean = digits.replace(/^0+/, "");
    const body = clean.startsWith(callingCode)
      ? clean.slice(callingCode.length)
      : clean;
    const international = `+${callingCode}${body}`;
    return new AsYouType(cc).input(international);
  } catch {
    return value;
  }
}

export function getFullPhone(value: string, countryCode: string): string {
  try {
    const parsed = parsePhoneNumberFromString(value, countryCode as CountryCode);
    if (parsed) return parsed.format("INTERNATIONAL");
  } catch {}
  return value.trim();
}

export function validateUsername(value: string): string | undefined {
  const v = value.trim();
  if (!v) return "Username is required";
  if (v.length < USERNAME_MIN || v.length > USERNAME_MAX)
    return `Username must be between ${USERNAME_MIN} and ${USERNAME_MAX} characters`;
  if (!USERNAME_RE.test(v))
    return "Only letters, numbers, underscores, and hyphens";
  return undefined;
}

export function validateEmail(value: string): string | undefined {
  const v = value.trim();
  if (!v) return "Email is required";
  if (!v.includes("@")) return "Email must contain an @";
  const [local, domain] = v.split("@");
  if (!local) return "Missing username before @";
  if (!domain) return "Missing domain after @";
  if (!domain.includes(".")) return "Domain must contain a dot";
  const parts = domain.split(".");
  const tld = parts[parts.length - 1].toLowerCase();
  if (!tld || tld.length < 2) return "Domain ending must be at least 2 letters";
  if (tld.length > 6 && !COMMON_EMAIL_TLDS.has(tld))
    return "Enter a valid email domain";
  if (!COMMON_EMAIL_TLDS.has(tld) && tld.length !== 2)
    return "Enter a valid email domain (e.g. .com, .org, .co.uk)";
  if (!EMAIL_RE.test(v)) return "Enter a valid email address";
  return undefined;
}

export function validatePhone(
  value: string,
  countryCode: string = DEFAULT_PHONE_COUNTRY
): string | undefined {
  const v = value.trim();
  if (!v) return "Phone number is required";
  try {
    if (isValidPhoneNumber(v, countryCode as CountryCode)) return undefined;
  } catch {}
  const label = countryNames.of(countryCode) ?? countryCode;
  return `Enter a valid ${label} phone number`;
}

/** Remove HTML tags to prevent stored XSS; enforce max length. */
export function sanitizeDisplayName(value: string): string {
  return value.replace(/<[^>]*>/g, "").trim();
}

export function validateDisplayName(value: string): string | undefined {
  const v = sanitizeDisplayName(value);
  if (v.length > DISPLAY_NAME_MAX)
    return `Display name must be at most ${DISPLAY_NAME_MAX} characters`;
  return undefined;
}

export type PasswordChecks = {
  length: boolean;
  upper: boolean;
  lower: boolean;
  digit: boolean;
  special: boolean;
  maxLength: boolean;
};

export function passwordChecks(value: string): PasswordChecks {
  return {
    length: value.length >= PASSWORD_MIN,
    upper: /[A-Z]/.test(value),
    lower: /[a-z]/.test(value),
    digit: /\d/.test(value),
    special: /[^A-Za-z0-9\s]/.test(value),
    maxLength: value.length <= PASSWORD_MAX,
  };
}

export function passwordIsValid(value: string): boolean {
  const c = passwordChecks(value);
  return c.length && c.upper && c.lower && c.digit && c.special && c.maxLength;
}

export function validatePassword(value: string): string | undefined {
  if (value.length < PASSWORD_MIN)
    return `Password must be at least ${PASSWORD_MIN} characters`;
  if (value.length > PASSWORD_MAX)
    return `Password must be at most ${PASSWORD_MAX} characters`;
  if (!/[A-Z]/.test(value)) return "Password must contain an uppercase letter";
  if (!/[a-z]/.test(value)) return "Password must contain a lowercase letter";
  if (!/\d/.test(value)) return "Password must contain a digit";
  if (!/[^A-Za-z0-9\s]/.test(value)) return "Password must contain a special character";
  return undefined;
}
