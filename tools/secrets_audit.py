"""Is anything sensitive reachable from a browser, a bundle, or a log?

Run it before a deployment, and after any change to what the server sends a
client:

    python -m tools.secrets_audit          # exits non-zero on any finding

Four places are checked, because they are the four ways a secret has actually
escaped from systems like this one:

  1. the built frontend bundles       — a key inlined at build time
  2. the frontend source              — an `import.meta.env.VITE_*` read
  3. what the API sends unauthenticated clients
  4. the audit trail                  — a token logged "just for debugging"

Nothing here prints a secret. A finding names the file and the pattern; reading
the value is the reader's job, on a machine that is allowed to have it.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _mask(value: str) -> str:
    """Whether a credential is configured, and nothing about what it is.

    Not even a prefix. A diagnostic gets pasted into tickets and chat logs, and
    "it starts with sk-or-v1" is the kind of half-disclosure that reads as
    caution while still narrowing an attacker's search.
    """
    return f"set ({len(value)} chars)" if value else "unset"


def _configured() -> dict[str, str]:
    from services import config

    return {
        name: value
        for name, value in (
            ("OPENROUTER_API_KEY", config.OPENROUTER_API_KEY),
            ("RETELL_API_KEY", config.RETELL_API_KEY),
        )
        if value
    }


#: Things that must never appear in anything a browser receives. Shapes as well
#: as literal values, so a key that is not configured on THIS machine is still
#: caught in a bundle built on another one.
SHAPES = {
    "openrouter key": r"sk-or-v1-[A-Za-z0-9]{20,}",
    "openai key": r"\bsk-[A-Za-z0-9]{32,}",
    "provider key name": r"OPENROUTER_API_KEY|RETELL_API_KEY|OPENAI_API_KEY",
    "scrypt password hash": r"scrypt\$\d+\$",
    "hardcoded bearer": r"Authorization\s*:\s*['\"]Bearer\s+\S",
}


def _scan(text: str, secrets: dict[str, str]) -> list[str]:
    found = [label for label, pattern in SHAPES.items() if re.search(pattern, text)]
    found += [f"literal {name}" for name, value in secrets.items() if value in text]
    return found


def audit() -> list[str]:
    findings: list[str] = []
    secrets = _configured()

    # 1. Built bundles.
    bundles = [
        path
        for directory in ("apps/candidate/dist", "apps/recruiter/dist")
        for path in (ROOT / directory).rglob("*")
        if path.is_file()
    ]
    for path in bundles:
        for hit in _scan(path.read_text(encoding="utf-8", errors="ignore"), secrets):
            findings.append(f"built bundle {path.relative_to(ROOT)}: {hit}")

    # 2. Frontend source. `VITE_*` is called out separately: anything read
    #    through it is inlined into the bundle by definition, so it is a leak
    #    the moment someone puts a secret behind one.
    for path in sorted((ROOT / "apps").rglob("*.ts?")):
        if "node_modules" in path.parts or "dist" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for hit in _scan(text, secrets):
            findings.append(f"frontend source {path.relative_to(ROOT)}: {hit}")
        for name in set(re.findall(r"import\.meta\.env\.(VITE_\w+)", text)):
            findings.append(
                f"frontend source {path.relative_to(ROOT)}: reads {name}, which is "
                "inlined into the bundle — never put a credential behind one"
            )

    # 3. What the API hands an unauthenticated client.
    from fastapi.testclient import TestClient

    from services.api.app import app

    with TestClient(app) as client:
        for path in ("/api/health", "/api/demo/prompts", "/openapi.json"):
            body = client.get(path).text
            for hit in _scan(body, secrets):
                findings.append(f"public response {path}: {hit}")

    # 4. The audit trail.
    from services.data import audit as audit_log

    rows = audit_log.read_product(limit=100_000)
    blob = "\n".join(json.dumps(row) for row in rows)
    for hit in _scan(blob, secrets):
        findings.append(f"audit trail ({len(rows)} rows): {hit}")
    for label, pattern in (("an Authorization header", r"[Aa]uthorization"),
                           ("a bearer token", r"[Bb]earer\s+\S")):
        if re.search(pattern, blob):
            findings.append(f"audit trail: contains {label}")

    return findings


def main() -> int:
    from services import config

    print("Tara secrets audit")
    print(f"  environment      : {config.ENVIRONMENT}")
    print(f"  provider key     : {_mask(config.OPENROUTER_API_KEY)}")
    print(f"  retell key       : {_mask(config.RETELL_API_KEY)}")
    print(f"  cookies secure   : {config.COOKIES_SECURE}")
    print(f"  allowed origins  : {', '.join(config.ALLOWED_ORIGINS)}")
    print()

    bundles = [d for d in ("apps/candidate/dist", "apps/recruiter/dist") if (ROOT / d).exists()]
    if not bundles:
        print("⚠ no built bundles found — run `npm run build` first, or this "
              "audit cannot see what a browser would receive.")

    findings = audit()
    for problem in config.require_production_configuration():
        findings.append(f"production configuration: {problem}")

    if findings:
        print(f"{len(findings)} finding(s):")
        for finding in findings:
            print(f"  ✗ {finding}")
        return 1
    print("No findings. Nothing sensitive in the bundles, the public responses, "
          "or the audit trail.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
