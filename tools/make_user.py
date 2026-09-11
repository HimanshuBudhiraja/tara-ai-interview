#!/usr/bin/env python3
"""Create an organization and a user. The way the first account exists.

    python -m tools.make_user --email you@example.com --admin
    python -m tools.make_user --email colleague@example.com --org org_default
    python -m tools.make_user --list

The password is read from a prompt, never from an argument — a password in
`argv` is a password in the shell history and in `ps`. `--password-stdin` is
there for a provisioning script, which should pipe a secret rather than embed
one.

Nothing here prints a password back, and the stored form is scrypt with a
per-user salt.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.data import accounts  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email")
    ap.add_argument("--org", default="", help="an existing organization id")
    ap.add_argument("--org-name", default="", help="create this organization instead")
    ap.add_argument("--admin", action="store_true", help="organization administrator")
    ap.add_argument("--viewer", action="store_true", help="read-only")
    ap.add_argument("--password-stdin", action="store_true",
                    help="read the password from stdin rather than a prompt")
    ap.add_argument("--list", action="store_true", help="show organizations and users")
    ap.add_argument("--disable", default="", help="revoke a user's logins by user id")
    args = ap.parse_args()

    if args.list:
        for org in accounts.list_organizations():
            print(f"{org.organization_id}  {org.name}  [{org.status}]")
            for user in accounts.list_users(org.organization_id):
                seen = "never" if not user.last_login_at else f"{user.last_login_at:.0f}"
                print(f"    {user.user_id}  {user.email:34} {user.role:10} "
                      f"[{user.status}]  last login {seen}")
        if not accounts.list_organizations():
            print("no organizations yet")
        return 0

    if args.disable:
        user = accounts.set_status(args.disable, accounts.DISABLED)
        if user is None:
            print(f"no user {args.disable}", file=sys.stderr)
            return 1
        revoked = accounts.revoke_sessions_for(user.user_id)
        print(f"disabled {user.email} and revoked {revoked} live session(s)")
        return 0

    if not args.email:
        ap.error("--email is required unless --list or --disable is given")

    if args.org_name:
        org = accounts.create_organization(args.org_name)
    elif args.org:
        org = accounts.get_organization(args.org)
        if org is None:
            print(f"no organization {args.org}", file=sys.stderr)
            return 1
    else:
        org = accounts.ensure_default_organization()

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("password (at least 12 characters): ")
        if password != getpass.getpass("again: "):
            print("those did not match", file=sys.stderr)
            return 1

    role = accounts.ADMIN if args.admin else (
        accounts.VIEWER if args.viewer else accounts.RECRUITER
    )
    try:
        user = accounts.create_user(org.organization_id, args.email, password, role=role)
    except accounts.AccountError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"created {user.email} as {user.role} in {org.name} ({org.organization_id})")
    print(f"user id {user.user_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
