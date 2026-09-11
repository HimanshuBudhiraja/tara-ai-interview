"""Server-side units of the Tara AI Interview product.

    api/            HTTP surface: candidate routes + recruiter routes
    orchestrator/   the candidate runtime — the only unit holding a session
    ai/             the AI Model Gateway and the workloads that use it
    data/           repositories: interviews, versions, invitations, sessions, audit
    evaluation/     scoring and analytics over recorded evidence

The dependency direction is one-way: api → {orchestrator, ai, data, evaluation},
and everything → packages/. The orchestrator never imports api.
"""
