"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { Check, LogIn, UserPlus, X } from "lucide-react";
import * as api from "@/lib/api";
import {
  DEFAULT_PHONE_COUNTRY,
  formatPhoneNumber,
  getFullPhone,
  getPhoneCountry,
  passwordChecks,
  passwordIsValid,
  PHONE_COUNTRIES,
  type PhoneCountry,
  sanitizeDisplayName,
  validateDisplayName,
  validateEmail,
  validatePassword,
  validatePhone,
  validateUsername,
} from "@/lib/validation";

type FormErrors = {
  username?: string;
  email?: string;
  phone?: string;
  displayName?: string;
  password?: string;
  confirmPassword?: string;
};

export function LoginDialog({ children }: { children?: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [isRegister, setIsRegister] = useState(false);
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [phoneCountry, setPhoneCountry] = useState(DEFAULT_PHONE_COUNTRY);
  const [countryQuery, setCountryQuery] = useState(() => {
    const c = getPhoneCountry(DEFAULT_PHONE_COUNTRY);
    return c ? `${c.label} ${c.name}` : "";
  });
  const [countryOpen, setCountryOpen] = useState(false);
  const [countryActive, setCountryActive] = useState(0);
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [errors, setErrors] = useState<FormErrors>({});
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [usernameStatus, setUsernameStatus] = useState<"idle" | "checking" | "available" | "taken" | "invalid">("idle");
  const [emailStatus, setEmailStatus] = useState<"idle" | "checking" | "available" | "taken" | "invalid">("idle");
  const [loading, setLoading] = useState(false);
  const { login, register } = useAuth();
  const usernameDebounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const emailDebounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const phoneCountryRef = useRef(phoneCountry);
  useEffect(() => { phoneCountryRef.current = phoneCountry; }, [phoneCountry]);

  const checks = passwordChecks(password);
  const selectedCountry = getPhoneCountry(phoneCountry);
  const displayFor = (c: PhoneCountry) => `${c.label} ${c.name} (${c.code})`;
  const filteredCountries = useMemo(() => {
    const q = countryQuery.toLowerCase().trim();
    if (!q) return PHONE_COUNTRIES;
    return PHONE_COUNTRIES.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.code.toLowerCase().includes(q) ||
        c.label.replace("+", "").includes(q)
    );
  }, [countryQuery]);
  const confirmMismatch = isRegister && confirmPassword.length > 0 && confirmPassword !== password;

  useEffect(() => {
    const c = getPhoneCountry(phoneCountry);
    setCountryQuery(c ? displayFor(c) : "");
  }, [phoneCountry]);

  // Debounced, case-insensitive username availability + format check.
  useEffect(() => {
    if (!isRegister) return;
    if (usernameDebounce.current) clearTimeout(usernameDebounce.current);
    const localError = validateUsername(username);
    if (localError) {
      setUsernameStatus("invalid");
      setErrors((prev) => ({ ...prev, username: localError }));
      return;
    }
    setUsernameStatus("checking");
    setErrors((prev) => ({ ...prev, username: undefined }));
    usernameDebounce.current = setTimeout(async () => {
      try {
        const res = await api.checkUsernameAvailable(username.trim());
        setUsernameStatus(res.available ? "available" : "taken");
        setErrors((prev) => ({
          ...prev,
          username: res.available ? undefined : "That username is already taken",
        }));
      } catch {
        setUsernameStatus("idle");
      }
    }, 400);
    return () => {
      if (usernameDebounce.current) clearTimeout(usernameDebounce.current);
    };
  }, [username, isRegister]);

  // Debounced email format + availability check.
  useEffect(() => {
    if (!isRegister) return;
    if (emailDebounce.current) clearTimeout(emailDebounce.current);
    const localError = validateEmail(email);
    if (localError) {
      setEmailStatus("invalid");
      setErrors((prev) => ({ ...prev, email: localError }));
      return;
    }
    setEmailStatus("checking");
    setErrors((prev) => ({ ...prev, email: undefined }));
    emailDebounce.current = setTimeout(async () => {
      try {
        const res = await api.checkEmailAvailable(email.trim());
        setEmailStatus(res.available ? "available" : "taken");
        setErrors((prev) => ({
          ...prev,
          email: res.available ? undefined : "That email is already in use",
        }));
      } catch {
        setEmailStatus("idle");
      }
    }, 400);
    return () => {
      if (emailDebounce.current) clearTimeout(emailDebounce.current);
    };
  }, [email, isRegister]);

  // Live local validation for phone, display name, password, confirm.
  useEffect(() => {
    if (!isRegister) return;
    setErrors((prev) => ({
      ...prev,
      phone: touched.phone ? validatePhone(phone, phoneCountry) : undefined,
      displayName: touched.displayName ? validateDisplayName(displayName) : undefined,
      password: touched.password ? validatePassword(password) : undefined,
      confirmPassword:
        touched.confirmPassword && confirmPassword !== password && confirmPassword.length > 0
          ? "Passwords do not match"
          : undefined,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isRegister, phone, phoneCountry, displayName, password, confirmPassword, touched]);

  const computeErrors = (): FormErrors => {
    const next: FormErrors = {};
    next.username = validateUsername(username);
    if (isRegister) {
      next.email = validateEmail(email);
      next.phone = validatePhone(phone, phoneCountry);
      next.displayName = validateDisplayName(displayName);
      next.password = validatePassword(password);
      next.confirmPassword =
        confirmPassword !== password ? "Passwords do not match" : undefined;
    } else if (!password) {
      next.password = "Password is required";
    }
    return next;
  };

  // Live validity used to enable/disable the submit button.
  const canSubmit = useMemo(() => {
    if (loading) return false;
    if (!isRegister) return username.trim().length > 0 && password.length > 0;
    return (
      !validateUsername(username) &&
      usernameStatus === "available" &&
      !validateEmail(email) &&
      emailStatus === "available" &&
      !validatePhone(phone, phoneCountry) &&
      !validateDisplayName(displayName) &&
      passwordIsValid(password) &&
      confirmPassword === password &&
      confirmPassword.length > 0
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isRegister, username, email, phone, phoneCountry, displayName, password, confirmPassword, usernameStatus, emailStatus, loading]);

  const touch = (field: keyof FormErrors) =>
    setTouched((prev) => ({ ...prev, [field]: true }));

  const blur = (field: keyof FormErrors) => {
    if (!isRegister) return;
    touch(field);
    if (field !== "username" && field !== "email") {
      setErrors((prev) => ({ ...prev, [field]: computeErrors()[field] }));
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const next = computeErrors();
    const final = {
      ...next,
      username:
        usernameStatus === "taken"
          ? "That username is already taken"
          : next.username,
      email:
        emailStatus === "taken"
          ? "That email is already in use"
          : next.email,
    };
    setErrors(final);
    if (Object.values(final).some((v) => v)) return;
    if (isRegister && (usernameStatus !== "available" || emailStatus !== "available")) return;
    setLoading(true);
    try {
      if (isRegister) {
        await register({
          username: username.trim(),
          email: email.trim(),
          phone: getFullPhone(phone, phoneCountry),
          display_name: sanitizeDisplayName(displayName) || undefined,
          password,
          confirm_password: confirmPassword,
        });
        toast.success("Account created and logged in!");
      } else {
        await login(username.trim(), password);
        toast.success("Welcome back!");
      }
      setOpen(false);
    } catch (err: any) {
      toast.error(err?.message || "Authentication failed");
    } finally {
      setLoading(false);
    }
  };

  const toggleMode = () => {
    setIsRegister(!isRegister);
    setErrors({});
    setTouched({});
    setUsernameStatus("idle");
    setEmailStatus("idle");
    setPhoneCountry(DEFAULT_PHONE_COUNTRY);
    setPassword("");
    setConfirmPassword("");
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {children || (
          <Button>
            <LogIn className="mr-2 h-4 w-4" />
            Sign In
          </Button>
        )}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isRegister ? "Create Account" : "Sign In"}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 mt-4">
          <div className="space-y-1">
            <label className="text-sm font-medium">Username</label>
            <Input
              value={username}
              onChange={(e) => { setUsername(e.target.value); touch("username"); }}
              onBlur={() => blur("username")}
              placeholder="Enter your username"
              autoComplete="username"
              className={touched.username ? (errors.username ? "border-red-500" : usernameStatus === "available" ? "border-green-600" : "") : ""}
            />
            {isRegister && (
              <>
                {errors.username && touched.username ? (
                  <p className="text-xs text-red-500">{errors.username}</p>
                ) : usernameStatus === "checking" ? (
                  <p className="text-xs text-muted-foreground">Checking availability…</p>
                ) : usernameStatus === "available" ? (
                  <p className="text-xs text-green-600">Username is available</p>
                ) : null}
              </>
            )}
          </div>
          {isRegister && (
            <>
              <div className="space-y-1">
                <label className="text-sm font-medium">Email</label>
                <Input
                  type="email"
                  value={email}
                  onChange={(e) => { setEmail(e.target.value); touch("email"); }}
                  onBlur={() => blur("email")}
                  placeholder="you@example.com"
                  autoComplete="email"
                  className={touched.email ? (errors.email ? "border-red-500" : emailStatus === "available" ? "border-green-600" : "") : ""}
                />
                {errors.email && touched.email ? (
                  <p className="text-xs text-red-500">{errors.email}</p>
                ) : emailStatus === "checking" ? (
                  <p className="text-xs text-muted-foreground">Checking availability…</p>
                ) : emailStatus === "available" ? (
                  <p className="text-xs text-green-600">Email looks good</p>
                ) : null}
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium">Phone</label>
                <div className="grid grid-cols-[12rem_1fr] gap-2">
                  <div className="relative h-9">
                    <Input
                      type="text"
                      value={countryQuery}
                      placeholder="Search Country or Code"
                      onFocus={() => {
                        setCountryQuery("");
                        setCountryOpen(true);
                        setCountryActive(0);
                      }}
                      onChange={(e) => {
                        const query = e.target.value;
                        setCountryQuery(query);
                        setCountryActive(0);
                        const match = PHONE_COUNTRIES.find(
                          (c) => displayFor(c).toLowerCase() === query.toLowerCase().trim()
                        );
                        if (match && match.code !== phoneCountryRef.current) {
                          phoneCountryRef.current = match.code;
                          setPhoneCountry(match.code);
                          setPhone(formatPhoneNumber(phone, match.code));
                          touch("phone");
                        }
                        setCountryOpen(true);
                      }}
                      onBlur={() => {
                        setCountryOpen(false);
                        setTimeout(() => {
                          const c = getPhoneCountry(phoneCountryRef.current);
                          setCountryQuery(c ? displayFor(c) : "");
                        }, 150);
                      }}
                      onKeyDown={(e) => {
                        if (
                          (e.key === "Tab" || e.key === "Enter") &&
                          countryOpen &&
                          filteredCountries.length > 0
                        ) {
                          const c = filteredCountries[countryActive];
                          phoneCountryRef.current = c.code;
                          setPhoneCountry(c.code);
                          setCountryQuery(displayFor(c));
                          setPhone("");
                          setCountryOpen(false);
                          setCountryActive(0);
                          touch("phone");
                        } else if (e.key === "Escape") {
                          setCountryOpen(false);
                        } else if (e.key === "ArrowDown" && countryOpen && filteredCountries.length > 0) {
                          e.preventDefault();
                          setCountryActive((i) => Math.min(i + 1, filteredCountries.length - 1));
                        } else if (e.key === "ArrowUp" && countryOpen && filteredCountries.length > 0) {
                          e.preventDefault();
                          setCountryActive((i) => Math.max(i - 1, 0));
                        }
                      }}
                      autoComplete="new-password"
                      aria-label="Phone country"
                      className="h-9 px-2 text-xs"
                    />
                    {countryOpen && filteredCountries.length > 0 && (
                      <div className="absolute z-50 mt-1 w-full max-h-80 overflow-y-auto rounded-md border border-input bg-background shadow-lg">
                        {filteredCountries.map((c, idx) => (
                          <div
                            key={c.code}
                            role="option"
                            aria-selected={c.code === phoneCountry}
                            onMouseEnter={() => setCountryActive(idx)}
                            onMouseDown={(e) => {
                              e.preventDefault();
                              phoneCountryRef.current = c.code;
                              setPhoneCountry(c.code);
                              setPhone(formatPhoneNumber(phone, c.code));
                              setCountryQuery(displayFor(c));
                              setCountryOpen(false);
                              touch("phone");
                            }}
                            className={`cursor-pointer px-2 py-1.5 text-xs hover:bg-muted ${countryActive === idx ? "bg-muted" : ""}`}
                          >
                            {displayFor(c)}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  <Input
                    type="tel"
                    value={phone}
                    onChange={(e) => { setPhone(formatPhoneNumber(e.target.value, phoneCountry)); touch("phone"); }}
                    onBlur={() => blur("phone")}
                    placeholder={(getPhoneCountry(phoneCountry) ?? PHONE_COUNTRIES[0]).placeholder}
                    autoComplete="off"
                    aria-label="Phone number"
                    className={touched.phone ? (errors.phone ? "border-red-500" : "border-green-600") : ""}
                  />
                </div>
                {errors.phone && touched.phone ? (
                  <p className="text-xs text-red-500">{errors.phone}</p>
                ) : touched.phone && !errors.phone ? (
                  <p className="text-xs text-green-600">Phone format valid</p>
                ) : null}
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium">Display Name (optional)</label>
                <Input
                  value={displayName}
                  onChange={(e) => { setDisplayName(e.target.value); touch("displayName"); }}
                  onBlur={() => blur("displayName")}
                  maxLength={100}
                  placeholder="Your public display name"
                  autoComplete="name"
                  className={touched.displayName && errors.displayName ? "border-red-500" : ""}
                />
                {errors.displayName && touched.displayName && (
                  <p className="text-xs text-red-500">{errors.displayName}</p>
                )}
              </div>
            </>
          )}
          <div className="space-y-1">
            <label className="text-sm font-medium">Password</label>
            <PasswordInput
              value={password}
              onChange={(e) => { setPassword(e.target.value); touch("password"); }}
              onBlur={() => blur("password")}
              maxLength={128}
              placeholder="Enter your password"
              autoComplete={isRegister ? "new-password" : "current-password"}
              className={touched.password ? (errors.password ? "border-red-500" : passwordIsValid(password) ? "border-green-600" : "") : ""}
            />
            {!isRegister && errors.password && (
              <p className="text-xs text-red-500">{errors.password}</p>
            )}
            {isRegister && (
              <ul className="mt-1 space-y-0.5" aria-label="Password requirements">
                <PasswordRule ok={checks.length} label="At least 8 characters" />
                <PasswordRule ok={checks.upper} label="An uppercase letter" />
                <PasswordRule ok={checks.lower} label="A lowercase letter" />
                <PasswordRule ok={checks.digit} label="A digit" />
                <PasswordRule ok={checks.special} label="A special character" />
                <PasswordRule ok={checks.maxLength} label="At most 128 characters" />
              </ul>
            )}
          </div>
          {isRegister && (
            <div className="space-y-1">
              <label className="text-sm font-medium">Confirm Password</label>
              <PasswordInput
                value={confirmPassword}
                onChange={(e) => { setConfirmPassword(e.target.value); touch("confirmPassword"); }}
                onBlur={() => blur("confirmPassword")}
                maxLength={128}
                placeholder="Re-enter your password"
                autoComplete="new-password"
                aria-invalid={confirmMismatch}
                className={touched.confirmPassword && confirmPassword ? (errors.confirmPassword ? "border-red-500" : "border-green-600") : ""}
              />
              {touched.confirmPassword && confirmPassword && errors.confirmPassword && (
                <p className="text-xs text-red-500">{errors.confirmPassword}</p>
              )}
            </div>
          )}
          <Button type="submit" className="w-full" disabled={!canSubmit}>
            {loading ? "Please wait..." : isRegister ? (
              <><UserPlus className="mr-2 h-4 w-4" /> Create Account</>
            ) : (
              <><LogIn className="mr-2 h-4 w-4" /> Sign In</>
            )}
          </Button>
          <button
            type="button"
            onClick={toggleMode}
            className="text-sm text-primary hover:underline w-full text-center"
          >
            {isRegister ? "Already have an account? Sign in" : "Need an account? Register"}
          </button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function PasswordRule({ ok, label }: { ok: boolean; label: string }) {
  return (
    <li className={`flex items-center gap-1.5 text-xs ${ok ? "text-green-600" : "text-red-500"}`}>
      {ok ? <Check className="h-3 w-3" aria-hidden="true" /> : <X className="h-3 w-3" aria-hidden="true" />}
      <span>{label}</span>
    </li>
  );
}
