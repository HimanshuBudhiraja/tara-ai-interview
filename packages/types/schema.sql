-- The target durable schema for Tara AI Interview.
--
-- STATUS: this is the shape, not the live store. Today the repositories in
-- services/data are file-backed JSON and the session store writes to disk; both
-- sit behind interfaces so swapping in Postgres does not reach past
-- services/data. This file exists so the domain model is reviewable now rather
-- than invented under deadline later, and so `docker compose --profile infra up`
-- stands up something real to look at.
--
-- Two structural decisions carry the product's guarantees:
--
--   1. interview_version holds the whole definition and is never updated. An
--      edit after publish inserts the next version. interview_session and
--      invitation both carry (interview_id, version), so what a candidate sat
--      is a fact on their row, not a lookup that changes underneath them.
--
--   2. evidence and skill_score are separate tables. Evidence is what happened;
--      a score is a judgement over it. Re-scoring produces new score rows and
--      leaves the evidence untouched — otherwise a re-run quietly rewrites what
--      the candidate said.

CREATE TABLE IF NOT EXISTS organization (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_user (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT 'recruiter'
                CHECK (role IN ('admin', 'recruiter', 'reviewer')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, email)
);

CREATE TABLE IF NOT EXISTS job (
    id               TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    title            TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    language         TEXT NOT NULL DEFAULT 'en',
    experience_from  INT  NOT NULL DEFAULT 0,
    experience_to    INT  NOT NULL DEFAULT 0,
    created_by       TEXT REFERENCES app_user(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The mutable container a recruiter edits. It holds no questions: questions
-- live on versions, because a question a candidate was asked must never change
-- after they were asked it.
CREATE TABLE IF NOT EXISTS interview (
    id               TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    job_id           TEXT REFERENCES job(id) ON DELETE SET NULL,
    title            TEXT NOT NULL,
    role             TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft', 'published', 'archived')),
    current_version  INT  NOT NULL DEFAULT 0,
    created_by       TEXT REFERENCES app_user(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Immutable. There is deliberately no UPDATE path in the repository for a
-- published row; correcting one means publishing the next.
CREATE TABLE IF NOT EXISTS interview_version (
    interview_id  TEXT NOT NULL REFERENCES interview(id) ON DELETE CASCADE,
    version       INT  NOT NULL,
    definition    JSONB NOT NULL,        -- the whole InterviewDefinition
    status        TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft', 'published')),
    checksum      TEXT NOT NULL,         -- content hash; detects tampering
    notes         TEXT NOT NULL DEFAULT '',
    published_at  TIMESTAMPTZ,
    published_by  TEXT REFERENCES app_user(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (interview_id, version)
);

-- Skills, tasks and questions are normalised out of the definition for
-- querying ("which interviews assess de-escalation?"). The definition JSONB
-- remains the source of truth for what a candidate actually sat — these rows
-- are an index over it, never a second copy to edit.
CREATE TABLE IF NOT EXISTS skill (
    id                  TEXT PRIMARY KEY,
    interview_id        TEXT NOT NULL,
    version             INT  NOT NULL,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    priority            TEXT NOT NULL DEFAULT 'medium'
                        CHECK (priority IN ('high', 'medium', 'low')),
    proficiency_target  INT  NOT NULL DEFAULT 2
                        CHECK (proficiency_target BETWEEN 0 AND 4),
    assessment_scope    TEXT NOT NULL DEFAULT '',
    question_bank       TEXT NOT NULL DEFAULT '',
    -- The skill master domain, frozen with the version. Free-text name,
    -- catalogued domain: the name is what a report is read against, the domain
    -- is what lets the same competency be counted across interviews.
    domain              TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (interview_id, version)
        REFERENCES interview_version(interview_id, version) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task (
    id            TEXT PRIMARY KEY,
    interview_id  TEXT NOT NULL,
    version       INT  NOT NULL,
    description   TEXT NOT NULL,
    priority      TEXT NOT NULL DEFAULT 'medium'
                  CHECK (priority IN ('high', 'medium', 'low')),
    outcome       TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (interview_id, version)
        REFERENCES interview_version(interview_id, version) ON DELETE CASCADE
);

-- Task → assesses → Skill. The many-to-many the designer must produce, and the
-- thing that makes a skill list checkable by a hiring manager.
CREATE INDEX IF NOT EXISTS skill_domain_idx ON skill (domain);

CREATE TABLE IF NOT EXISTS task_skill (
    task_id   TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    skill_id  TEXT NOT NULL REFERENCES skill(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, skill_id)
);

CREATE TABLE IF NOT EXISTS question (
    id                   TEXT PRIMARY KEY,
    interview_id         TEXT NOT NULL,
    version              INT  NOT NULL,
    question_text        TEXT NOT NULL,
    skill_id             TEXT REFERENCES skill(id) ON DELETE SET NULL,
    task_id              TEXT REFERENCES task(id) ON DELETE SET NULL,
    competency           TEXT NOT NULL DEFAULT '',
    difficulty           TEXT NOT NULL DEFAULT 'medium'
                         CHECK (difficulty IN ('easy', 'medium', 'hard')),
    expected_signal      TEXT NOT NULL DEFAULT '',
    looking_for          JSONB NOT NULL DEFAULT '[]',
    evaluation_criteria  JSONB NOT NULL DEFAULT '[]',
    probe_eligible       BOOLEAN NOT NULL DEFAULT true,
    max_probes           INT  NOT NULL DEFAULT 2,
    time_budget_sec      INT  NOT NULL DEFAULT 90,
    probe_bank           JSONB NOT NULL DEFAULT '[]',
    clarify              TEXT NOT NULL DEFAULT '',
    source               TEXT NOT NULL DEFAULT 'authored'
                         CHECK (source IN ('authored', 'generated', 'bank')),
    FOREIGN KEY (interview_id, version)
        REFERENCES interview_version(interview_id, version) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS candidate (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL DEFAULT '',
    external_ref  TEXT NOT NULL DEFAULT '',   -- the ATS's id for this person
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- interview_version is captured when the link is MINTED, not when it is opened.
CREATE TABLE IF NOT EXISTS invitation (
    token             TEXT PRIMARY KEY,
    interview_id      TEXT NOT NULL,
    interview_version INT  NOT NULL,
    candidate_id      TEXT NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','in_progress','complete','expired','revoked')),
    session_id        TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ,
    FOREIGN KEY (interview_id, interview_version)
        REFERENCES interview_version(interview_id, version)
);

CREATE TABLE IF NOT EXISTS interview_session (
    id                 TEXT PRIMARY KEY,
    interview_id       TEXT NOT NULL,
    interview_version  INT  NOT NULL,
    candidate_id       TEXT NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
    invitation_token   TEXT REFERENCES invitation(token) ON DELETE SET NULL,
    phase              TEXT NOT NULL DEFAULT 'created',
    channel            TEXT NOT NULL DEFAULT 'voice' CHECK (channel IN ('voice','text')),
    consent_recording  BOOLEAN NOT NULL DEFAULT false,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ,
    FOREIGN KEY (interview_id, interview_version)
        REFERENCES interview_version(interview_id, version)
);

CREATE TABLE IF NOT EXISTS interview_turn (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES interview_session(id) ON DELETE CASCADE,
    idx          INT  NOT NULL,
    speaker      TEXT NOT NULL CHECK (speaker IN ('tara', 'candidate')),
    text         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT '',
    question_id  TEXT REFERENCES question(id) ON DELETE SET NULL,
    at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, idx)
);

-- What happened. `answered = false` means the question was never answered — it
-- is excluded from scoring denominators, never treated as a zero.
CREATE TABLE IF NOT EXISTS evidence (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES interview_session(id) ON DELETE CASCADE,
    question_id   TEXT REFERENCES question(id) ON DELETE SET NULL,
    skill_id      TEXT REFERENCES skill(id) ON DELETE SET NULL,
    answer_text   TEXT NOT NULL DEFAULT '',
    probes_asked  JSONB NOT NULL DEFAULT '[]',
    covered       JSONB NOT NULL DEFAULT '[]',
    missing       JSONB NOT NULL DEFAULT '[]',
    quote         TEXT NOT NULL DEFAULT '',
    answered      BOOLEAN NOT NULL DEFAULT true,
    at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A judgement over evidence. Confidence is first-class: one thin answer and
-- four probing exchanges must not read as equally certain.
CREATE TABLE IF NOT EXISTS skill_score (
    id                    TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL REFERENCES interview_session(id) ON DELETE CASCADE,
    skill_id              TEXT REFERENCES skill(id) ON DELETE SET NULL,
    skill_name            TEXT NOT NULL,
    level                 NUMERIC(4,2) NOT NULL,
    scale                 INT NOT NULL DEFAULT 5,
    confidence            NUMERIC(3,2) NOT NULL DEFAULT 0,
    rationale             TEXT NOT NULL DEFAULT '',
    questions_answered    INT NOT NULL DEFAULT 0,
    questions_unanswered  INT NOT NULL DEFAULT 0,
    engine                TEXT NOT NULL DEFAULT 'deterministic',
    generated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS interview_result (
    id                 TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL REFERENCES interview_session(id) ON DELETE CASCADE,
    interview_id       TEXT NOT NULL,
    interview_version  INT  NOT NULL,
    composite          NUMERIC(4,2) NOT NULL DEFAULT 0,
    scale              INT NOT NULL DEFAULT 5,
    band               TEXT NOT NULL DEFAULT '',
    task_coverage      JSONB NOT NULL DEFAULT '{}',
    generated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS report (
    id                     TEXT PRIMARY KEY,
    session_id             TEXT NOT NULL REFERENCES interview_session(id) ON DELETE CASCADE,
    result_id              TEXT NOT NULL REFERENCES interview_result(id) ON DELETE CASCADE,
    summary                TEXT NOT NULL DEFAULT '',
    strengths              JSONB NOT NULL DEFAULT '[]',
    gaps                   JSONB NOT NULL DEFAULT '[]',
    recommended_followups  JSONB NOT NULL DEFAULT '[]',
    model                  TEXT NOT NULL DEFAULT '',
    generated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only. No UPDATE, no DELETE — an audit log you can edit is a log nobody
-- has to believe.
CREATE TABLE IF NOT EXISTS audit_event (
    id            TEXT PRIMARY KEY,
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    event         TEXT NOT NULL,
    actor         TEXT NOT NULL DEFAULT 'system',
    org_id        TEXT,
    subject_type  TEXT NOT NULL DEFAULT '',
    subject_id    TEXT NOT NULL DEFAULT '',
    data          JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_interview_org       ON interview(org_id);
CREATE INDEX IF NOT EXISTS idx_version_interview   ON interview_version(interview_id);
CREATE INDEX IF NOT EXISTS idx_session_interview   ON interview_session(interview_id, interview_version);
CREATE INDEX IF NOT EXISTS idx_evidence_session    ON evidence(session_id);
CREATE INDEX IF NOT EXISTS idx_score_session       ON skill_score(session_id);
CREATE INDEX IF NOT EXISTS idx_turn_session        ON interview_turn(session_id, idx);
CREATE INDEX IF NOT EXISTS idx_audit_subject       ON audit_event(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_audit_at            ON audit_event(at DESC);
