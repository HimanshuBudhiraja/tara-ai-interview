"""Authentication, authorization and the abuse boundary.

Kept apart from `services/api` because these are decisions about who may do
what, and a decision that lives inside a route handler is a decision nobody can
audit. The API layer asks this package; it does not re-implement it.
"""
from services.security.principal import (  # noqa: F401
    AuthenticatedPrincipal,
    CANDIDATE_COOKIE,
    SESSION_COOKIE,
    candidate_scope,
    current_principal,
    optional_principal,
    recruiter_scope,
    require_capability,
)
