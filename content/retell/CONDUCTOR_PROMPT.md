# Conductor prompt: build the Tara Agent Builder on one Retell agent

Paste everything below the line into Conductor, in a workspace on the `tara-platform` repo.

---

## Goal
Make the Tara Agent Builder prototype deployable on ONE generic Retell agent.

A recruiter writes a brief. Tara (our LLM) drafts an agent config. The recruiter edits the config, then tests it as a live Retell web call. Every agent built in the app runs on the same Retell agent. What makes each one different is the per-call `retell_llm_dynamic_variables` and `agent_override`. Never create a Retell agent per built agent.

## Read these first (source of truth)
1. `design/tara-agent-builder-prototype.html` is the approved UI, with three screens: brief + templates, build, then review / refine / test. Match its layout, fields and wording exactly. Its `window.claude.use('sample')` calls are stand-ins for our backend. Each one becomes an API call (the endpoints section below).
2. `content/retell/tara_agent_builder_prompt.md` is the Retell agent's global prompt (section C), the agent settings (section A), the 18-variable contract and the per-call overrides (section B). Do not rename any variable.
3. Existing code to reuse, not duplicate:
   - `services/api/builder.py`: route pattern and `builder_scope` / recruiter guard. New routes must also go in the `/api/admin` alias loop.
   - `services/assessment/retell_export.py`: the dynamic-variable export pattern and `leaked_cues` check.
   - `services/ai/workloads/scenario_builder.py`: workload / gateway pattern.
   - `services/data/built_scenarios.py`: storage pattern.
   - `services/api/retell.py`: the existing Retell client.
   - `.env`: `RETELL_API_KEY`, `RETELL_AGENT_ID`.

## Step 1: create the one Retell agent (idempotent script)
Add `tools/retell_setup_agent_builder.py`. It should:
- Create, or update if `RETELL_AGENT_BUILDER_LLM_ID` / `RETELL_AGENT_BUILDER_AGENT_ID` are already set, one Retell LLM and one agent named `Tara - Agent Builder (generic)`.
- Set `general_prompt` to section C of `tara_agent_builder_prompt.md`, read from the file at runtime (everything after the `## C.` heading's first line), and `begin_message` to `{{opening_line}}`. Turn the `end_call` tool on.
- Apply the section A settings: responsiveness 0.4, interruption_sensitivity 0.4, backchannel on (0.7; mm-hmm, I see, right), reminder 15000 ms ×1, language en-US, default voice `11labs-Adrian`.
- Print the two ids and write them to `.env` as `RETELL_AGENT_BUILDER_LLM_ID` and `RETELL_AGENT_BUILDER_AGENT_ID`. Leave the existing `RETELL_AGENT_ID` (the CSR interview agent) untouched.
- Support `--dry-run`, which prints the payloads without calling Retell.
- Before writing any code, check the current Retell API shapes in https://docs.retellai.com (create/update Retell LLM, create/update agent, create-web-call `agent_override`, list-voices).

Then fill in the voice table. Call `GET /list-voices`, then replace each `RETELL_VOICE_ID_*` placeholder with a real voice id that matches the label (gender, accent, language). Put the map in one place, `services/assessment/agent_builder_voices.py`, and make the UI read it from the API. If no good match exists for an option, drop that option and say so in your summary.

## Step 2: backend (`services/api/agent_builder.py`, mounted at `/api/recruiter/agent-builder/*`)
- `POST /drafts {brief, mode}`: run the LLM build (a new workload `agent_builder_design`) and return the agent JSON in exactly the prototype's `AGENT_SHAPE`, including `scenario`, `depth` and `voice`. Stream progress over SSE so screen 2's steps and draft card fill in live.
- `PATCH /agents/{id}`: save edits from screen 3.
- `POST /agents/{id}/revise {instruction}`: the "Ask Tara" bar. Return the updated agent plus a one-line summary.
- `POST /agents/{id}/questions:generate`: "Generate more", which adds 3 questions.
- `POST /agents/{id}/test-call`: build the create-web-call request exactly like the prototype's `callConfig()` and return Retell's `access_token` for the web client:
  - `agent_id` = `RETELL_AGENT_BUILDER_AGENT_ID`
  - `agent_override.agent.voice_id` + `language` come from **Persona → Voice**
  - `agent_override.agent.max_call_duration_ms` = (max_minutes + 2) × 60000
  - `target_minutes` comes from **Persona → Follow-up depth**: Light 10, Probing 20, Deep dive 30. `max_minutes` = target + 5 ("Tara decides"), = target ("Hard time limit"), or 60 ("No end time").
  - `agent_override.retell_llm.start_speaker` = `agent` | `user` from "Who speaks first". `begin_message` is the opening line, or `""` when the participant opens.
  - All 18 `retell_llm_dynamic_variables`, every one a string, none missing (`""` if empty).
- `POST /agents/{id}/publish`: allowed only when the readiness checklist passes (scenario details complete, description, instructions, persona name, rubric reviewed and weights totalling 100).
- Retell call-ended webhook: store the transcript, then score it against the rubric server-side (reuse the scoring engine). This replaces the prototype's "Score this test" call.

## Step 3: frontend (`apps/agent-builder/`)
Port the prototype's three screens as-is. Replace every `sample(...)` / `sample.json(...)` with the endpoints above. Keep the chat test as a fallback, and add a real **Voice** test tab that uses the Retell web client SDK with the `access_token` from `/test-call`. The "Retell call config" panel shows the server's actual request body.

## Hard rules (add tests for each)
- The rubric (names, anchors, weights) and question tags NEVER go into the Retell payload. Run `leaked_cues` over every payload and fail the request if it finds anything.
- Exactly the 18 variables in `tara_agent_builder_prompt.md` are sent, all strings. Add a test that renders the general prompt with a payload and asserts no `{{` is left.
- Voice comes only from Persona → Voice. Length comes only from Persona → Follow-up depth (+ Ending for the cap). Never derive either from difficulty, language or question count.
- One Retell agent in total. The test fails if the code path can create an agent per draft.
- Recruiter-guarded routes. Untrusted brief and edit text is fenced before it goes into any LLM prompt (see `services/ai/workloads/untrusted.py`).

## Done when
1. `tools/retell_setup_agent_builder.py --dry-run` prints valid payloads, and a real run creates the agent and writes its ids to `.env`.
2. Brief → build → edit → Test agent places a real Retell web call that speaks in the chosen voice, honours the depth-based length, and stays in persona.
3. Existing tests plus the new ones pass. Stop and ask me before committing or pushing.
