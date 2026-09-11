import { useCallback, useEffect, useRef, useState } from "react";
import { TriangleAlert, Wand2 } from "lucide-react";
import {
  adminApi,
  type ExtractInput,
  type Interview,
  type PoolPayload,
  type Skill,
} from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Button, Callout, Spinner } from "@tara/ui/primitives";
import { DifficultyPill, StatusPill } from "../components/RecruiterShell";
import { JobDetails } from "../sections/JobDetails";
import { OutcomesTasks } from "../sections/OutcomesTasks";
import { SkillsEditor } from "../sections/SkillsEditor";
import { Section, Slider, Tick } from "../sections/bits";
import { TestRun } from "../sections/TestRun";
import { cn } from "../lib/cn";

/**
 * Configure an interview, from a job description.
 *
 *   job details + JD  →  outcomes / tasks / skills  →  adjust  →  conversation
 *                     →  preview  →  publish
 *
 * A form, not a wizard. Wizards suit one-time setup; a recruiter comes back to
 * tune an interview after watching the first few candidates go through it, and
 * a wizard makes that miserable.
 *
 * The preview is the reason the screen works. Selection is deterministic, so
 * the right-hand panel isn't a simulation — it runs the same code path a live
 * interview takes and shows the exact questions, in order, that this
 * configuration produces.
 */
export function InterviewBuilder({ id }: { id: string }) {
  const [pool, setPool] = useState<PoolPayload | null>(null);
  const [iv, setIv] = useState<Interview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [extractError, setExtractError] = useState<string | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [saving, setSaving] = useState(false);

  const pending = useRef<Record<string, unknown>>({});
  const timer = useRef<number | null>(null);

  useEffect(() => {
    Promise.all([adminApi.pool(), adminApi.interview(id)])
      .then(([p, i]) => {
        setPool(p);
        setIv(i);
      })
      .catch((e) => setError(e.message));
  }, [id]);

  /**
   * Debounced write-through. Every control saves rather than hiding behind a
   * Save button, because the preview has to reflect reality — a preview
   * computed from unsaved local state would eventually disagree with what
   * candidates actually get, which is the one thing it exists to prevent.
   */
  const patch = useCallback(
    (changes: Record<string, unknown>) => {
      pending.current = { ...pending.current, ...changes };
      setIv((prev) => (prev ? applyLocally(prev, changes) : prev));
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(async () => {
        const body = pending.current;
        pending.current = {};
        setSaving(true);
        try {
          setIv(await adminApi.updateInterview(id, body));
          setError(null);
        } catch (e: any) {
          setError(e.message);
        } finally {
          setSaving(false);
        }
      }, 450);
    },
    [id],
  );

  const patchSkill = useCallback(
    (competency_id: string, changes: Partial<Skill>) => {
      setIv((prev) =>
        prev
          ? {
              ...prev,
              skills: prev.skills.map((s) =>
                s.competency_id === competency_id ? { ...s, ...changes } : s,
              ),
            }
          : prev,
      );
      // Skill edits go one at a time rather than accumulating into `pending`:
      // the PATCH body is a list keyed by competency_id, and merging two edits
      // to different skills into one debounced payload silently drops the first.
      void adminApi
        .updateInterview(id, { skills: [{ competency_id, ...changes }] })
        .then(setIv)
        .catch((e) => setError(e.message));
    },
    [id],
  );

  const extract = useCallback(
    async (input: ExtractInput) => {
      setExtracting(true);
      setExtractError(null);
      try {
        setIv(await adminApi.extract(id, input));
      } catch (e: any) {
        setExtractError(e.message);
      } finally {
        setExtracting(false);
      }
    },
    [id],
  );

  if (error && !iv) return <Callout tone="error">{error}</Callout>;
  if (!pool || !iv) return <div className="card h-96 animate-pulse bg-gray-50" />;

  const preview = iv.preview;

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_384px]">
      {/* ------------------------------ configuration ------------------------------ */}
      <div className="min-w-0 space-y-4">
        {error && <Callout tone="error">{error}</Callout>}

        <JobDetails
          iv={iv}
          onPatch={patch}
          onExtract={extract}
          extracting={extracting}
          error={extractError}
        />

        {!iv.extracted && !extracting && (
          <Callout tone="info" title="Nothing configured yet">
            Paste the job description above and analyse it. TARA works out what the role achieves,
            the tasks behind it, and the skills worth interviewing — then you adjust all of it.
          </Callout>
        )}

        <OutcomesTasks iv={iv} />

        <SkillsEditor
          iv={iv}
          pool={pool}
          onPatchSkill={patchSkill}
          onRemove={(cid) =>
            void adminApi
              .updateInterview(id, { remove_skills: [cid] })
              .then(setIv)
              .catch((e) => setError(e.message))
          }
          onAdd={(name) =>
            void adminApi
              .updateInterview(id, { add_skill: name })
              .then(setIv)
              .catch((e) => setError(e.message))
          }
          onReset={() =>
            void adminApi
              .resetSkills(id)
              .then(setIv)
              .catch((e) => setError(e.message))
          }
        />

        {iv.extracted && (
          <Section title="The conversation" hint="How long it runs and how hard TARA pushes.">
            <div className="grid gap-5 sm:grid-cols-2">
              <Slider
                label="Questions per candidate"
                value={iv.question_budget}
                min={1}
                max={pool.items.length}
                onChange={(v) => patch({ question_budget: v })}
                note={`Roughly ${preview?.estimated_minutes ?? "—"} minutes with follow-ups.`}
              />
              <Slider
                label="Follow-ups per question"
                value={iv.max_probes_per_item}
                min={0}
                max={4}
                onChange={(v) => patch({ max_probes_per_item: v })}
                note={
                  iv.max_probes_per_item === 0
                    ? "TARA will never probe. She'll take the first answer and move on."
                    : "A ceiling, not a target — strong answers get fewer."
                }
              />
            </div>

            <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-lg border border-gray-200 p-3">
              <Tick
                checked={iv.allow_generated_probes}
                onChange={(v) => patch({ allow_generated_probes: v })}
              />
              <span>
                <span className="block text-[15px] font-medium text-gray-900">
                  Let TARA write follow-ups from the candidate's answer
                </span>
                <span className="mt-0.5 block text-sm leading-relaxed text-gray-500">
                  On: follow-ups reference what the candidate actually said, and every one is
                  checked for legality, relevance, and length before it's spoken. Off: only the
                  authored follow-ups are ever used.
                </span>
              </span>
            </label>
          </Section>
        )}

        <TestRun interviewId={id} disabled={!iv.extracted} />
      </div>

      {/* --------------------------------- preview --------------------------------- */}
      <aside className="xl:sticky xl:top-[86px] xl:self-start">
        <div className="card overflow-hidden">
          <div className="border-b border-gray-200 px-4 py-3.5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-md font-semibold text-gray-900">The interview</h2>
                <p className="meta mt-0.5 flex items-center gap-1.5">
                  {saving && <Spinner className="h-3 w-3" />}
                  {saving ? "Saving…" : "Exactly what TARA will ask"}
                </p>
              </div>
              <StatusPill status={iv.status} />
            </div>
          </div>

          <div className="grid grid-cols-3 divide-x divide-gray-200 border-b border-gray-200 bg-gray-25 text-center">
            <Metric value={preview?.items.length ?? 0} label="Questions" />
            <Metric value={`~${preview?.estimated_minutes ?? "—"}`} label="Minutes" />
            <Metric value={iv.covered_count} label="Skills" />
          </div>

          {preview?.warnings.length ? (
            <div className="space-y-1.5 border-b border-warning-200 bg-warning-25 px-4 py-3">
              {preview.warnings.map((w) => (
                <p key={w} className="flex gap-2 text-xs leading-relaxed text-warning-700">
                  <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0" />
                  {w}
                </p>
              ))}
            </div>
          ) : null}

          {preview?.skills.length ? (
            <div className="border-b border-gray-200 px-4 py-3">
              <p className="label mb-2">Skills assessed</p>
              <ul className="space-y-1.5">
                {preview.skills.map((s) => (
                  <li key={s.competency_id} className="flex items-center gap-2 text-xs">
                    <span
                      className={cn(
                        "h-1.5 w-1.5 shrink-0 rounded-full",
                        s.questions_planned > 0 ? "bg-success-500" : "bg-warning-500",
                      )}
                      title={s.questions_planned > 0 ? "Covered" : "No questions behind it"}
                    />
                    <span className="flex-1 truncate font-medium text-gray-700">{s.name}</span>
                    <span className="shrink-0 capitalize text-gray-400">{s.priority}</span>
                    <span className="tabular w-7 shrink-0 text-right font-semibold text-gray-500">
                      {s.questions_planned}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <ol className="max-h-[360px] divide-y divide-gray-100 overflow-y-auto">
            {preview?.items.map((p) => (
              <li key={p.id} className="px-4 py-2.5 transition-colors hover:bg-gray-25">
                <div className="mb-1 flex items-center gap-2">
                  <span className="tabular grid h-4 w-4 shrink-0 place-items-center rounded-xs bg-gray-100 text-2xs font-bold text-gray-500">
                    {p.position}
                  </span>
                  <span className="truncate text-xs font-medium text-gray-500">
                    {p.skills[0] ?? p.competency_label}
                  </span>
                  <DifficultyPill level={p.difficulty} />
                </div>
                <p className="pl-6 text-sm leading-relaxed text-gray-700">{p.prompt}</p>
              </li>
            ))}
            {!preview?.items.length && (
              <li className="px-5 py-10 text-center text-sm text-gray-400">
                {iv.extracted
                  ? "Nothing will be asked. Map at least one evaluated skill to a question bank."
                  : "Analyse a job description to see the interview."}
              </li>
            )}
          </ol>

          <div className="border-t border-gray-200 bg-gray-25 p-3.5">
            {iv.status === "draft" ? (
              <>
                <Button
                  className="w-full"
                  disabled={!preview?.items.length}
                  onClick={() => patch({ status: "published" })}
                >
                  <Wand2 className="h-4 w-4" />
                  Publish &amp; invite candidates
                </Button>
                <p className="meta mt-2 text-center">
                  You can't invite anyone until it's published.
                </p>
              </>
            ) : (
              <div className="flex gap-2">
                <Button
                  className="flex-1"
                  onClick={() => navigate({ name: "interview", id, tab: "candidates" })}
                >
                  Invite candidates
                </Button>
                <Button variant="secondary" onClick={() => patch({ status: "draft" })}>
                  Unpublish
                </Button>
              </div>
            )}
          </div>
        </div>
      </aside>
    </div>
  );
}

/** Optimistic local echo, so typing doesn't wait on the round trip. */
function applyLocally(iv: Interview, changes: Record<string, unknown>): Interview {
  const next: Interview = { ...iv };
  const company = { ...iv.company };
  let touchedCompany = false;
  for (const [key, value] of Object.entries(changes)) {
    if (key === "company_name") {
      company.name = String(value);
      touchedCompany = true;
    } else if (key === "company_about") {
      company.about = String(value);
      touchedCompany = true;
    } else if (key === "role_context") {
      company.role_context = String(value);
      touchedCompany = true;
    } else {
      (next as any)[key] = value;
    }
  }
  if (touchedCompany) next.company = company;
  return next;
}

function Metric({ value, label }: { value: React.ReactNode; label: string }) {
  return (
    <div className="py-3">
      <p className="tabular text-xl font-semibold leading-none text-gray-900">{value}</p>
      <p className="label mt-1.5">{label}</p>
    </div>
  );
}
