"""The six AI workload boundaries (§13).

    interview_designer    JD → outcomes, tasks, skills          design time
    question_generator    skills + tasks → questions            design time
    answer_classifier     one candidate turn → intent, depth     runtime
    followup_generator    one turn → one constrained probe       runtime
    scoring_engine        evidence → levels + confidence         post-interview
    report_generator      levels + evidence → narrative          post-interview

Each is an independent module with its own prompt, its own schema, and its own
configurable model. None of them holds interview state: they are functions over
what they are handed. The orchestrator owns the session, and it stays that way.
"""
