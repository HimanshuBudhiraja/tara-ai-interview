import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, ArrowLeft, Check, ListChecks, Lock, Plus, RefreshCw, Sparkles,
  Trash2, X,
} from "lucide-react";
import {
  adminApi, FormError, GenerationError,
  type Difficulty, type NewQuestionInput, type PoolQuestion, type Priority,
  type QuestionPool, type QuestionPatchInput, type QuestionType,
} from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Badge, Button, Callout, Spinner } from "@tara/ui/primitives";
import { DifficultyPill, SectionHeading, Stat } from "../components/RecruiterShell";
import { cn } from "../lib/cn";

const TYPE_LABEL: Record<QuestionType, string> = {
  behavioral: "Behavioural",
  situational: "Situational",
  technical: "Technical",
  task_based: "Task-based",
};

/**
 * The question pool, grouped by the skill each question assesses.
 *
 * Grouped rather than listed flat because reviewing a pool is checking whether
 * each skill is properly covered — a flat list of twenty questions is a thing
 * you scroll past, not a thing you approve.
 */
export function QuestionPoolScreen({ id }: { id: string }) {
  const [pool, setPool] = useState<QuestionPool | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [showOrder, setShowOrder] = useState(false);

  useEffect(() => {
    let live = true;
    adminApi.questions(id)
      .then((p) => live && setPool(p))
      .catch((e: Error) => live && setError(e.message));
    return () => { live = false; };
  }, [id]);

  const run = useCallback(
    async (label: string, action: () => Promise<QuestionPool>) => {
      setBusy(label);
      setError(null);
      try {
        setPool(await action());
      } catch (err) {
        setError(
          err instanceof FormError
            ? Object.values(err.errors).flat().join(" ")
            : err instanceof GenerationError
              ? err.message
              : (err as Error).message,
        );
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  if (error && !pool) return <Callout tone="error" title="Couldn't open the pool">{error}</Callout>;
  if (!pool) return <div className="card grid place-items-center py-20"><Spinner /></div>;

  if (busy === "generate") return <Generating role={pool.role_title} />;

  const { summary, coverage } = pool;
  const fatal = coverage.problems.filter((p) => p.fatal);
  const warnings = coverage.problems.filter((p) => !p.fatal);

  return (
    <div className="max-w-5xl space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button variant="link" onClick={() => navigate({ name: "recommended", id })}>
          <ArrowLeft className="h-4 w-4" />
          Recommended interview
        </Button>
        <div className="flex items-center gap-2">
          {busy && <Spinner className="text-gray-400" />}
          {pool.generated && (
            <Button variant="secondary" size="sm"
                    onClick={() => run("generate", () => adminApi.generateQuestions(id))}>
              <RefreshCw className="h-4 w-4" />
              Regenerate all
            </Button>
          )}
        </div>
      </div>

      {error && <Callout tone="error" title="That didn't work">{error}</Callout>}

      {!pool.generated ? (
        <Empty onGenerate={() => run("generate", () => adminApi.generateQuestions(id))} />
      ) : (
        <>
          {/* --- the numbers at the top --------------------------------- */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Pool size" value={summary.pool_size}
                  hint={`${summary.live_item_budget} asked per candidate`} accent />
            <Stat label="Interview duration" value={`${summary.target_duration_min} min`}
                  hint="Includes follow-up headroom" />
            <Stat label="Skills covered"
                  value={`${summary.skills_covered}/${summary.skills_total}`}
                  hint={`${summary.tasks_covered}/${summary.tasks_total} tasks grounded`} />
            <Stat label="Difficulty"
                  value={
                    <span className="text-md font-semibold">
                      {["easy", "medium", "hard"]
                        .map((d) => `${summary.difficulty_distribution[d] ?? 0}`)
                        .join(" · ")}
                    </span>
                  }
                  hint="easy · medium · hard" />
          </div>

          {fatal.length > 0 && (
            <Callout tone="error" title="This pool isn't ready yet">
              <ul className="ml-4 list-disc space-y-1">
                {fatal.map((p) => <li key={p.code}>{p.message}</li>)}
              </ul>
            </Callout>
          )}
          {warnings.length > 0 && (
            <Callout tone="warning" title="Worth a look before you publish">
              <ul className="ml-4 list-disc space-y-1">
                {warnings.map((p, i) => <li key={`${p.code}-${i}`}>{p.message}</li>)}
              </ul>
            </Callout>
          )}
          {pool.report && pool.report.failed_slots.length > 0 && (
            <Callout tone="warning" title="Some questions couldn't be written">
              <ul className="ml-4 list-disc space-y-1">
                {pool.report.failed_slots.map((s) => (
                  <li key={s.slot_id}>{s.skill}: {s.error}</li>
                ))}
              </ul>
              <div className="mt-3">
                <Button size="sm" variant="secondary"
                        onClick={() => run("generate", () => adminApi.generateQuestions(
                          id, pool.report!.failed_slots.map((s) => s.slot_id)))}>
                  Retry those
                </Button>
              </div>
            </Callout>
          )}

          {/* --- what a candidate would actually be asked ---------------- */}
          <section className="card p-4">
            <button onClick={() => setShowOrder((s) => !s)}
                    className="flex w-full items-center justify-between text-left">
              <div>
                <p className="text-md font-semibold text-gray-900">
                  Expected running order
                </p>
                <p className="mt-0.5 text-sm text-gray-500">
                  The {pool.running_order.length} question
                  {pool.running_order.length === 1 ? "" : "s"} a candidate would be asked,
                  in order, from the same selection logic the live interview uses.
                </p>
              </div>
              <span className="text-sm font-medium text-brand-600">
                {showOrder ? "Hide" : "Show"}
              </span>
            </button>
            {showOrder && (
              <ol className="mt-3 space-y-1.5 border-t border-gray-100 pt-3">
                {pool.running_order.map((entry) => (
                  <li key={entry.question_id} className="flex gap-2.5 text-sm">
                    <span className="tabular w-5 shrink-0 text-gray-400">{entry.position}.</span>
                    <DifficultyPill level={entry.difficulty} />
                    <span className="shrink-0 text-gray-500">{entry.skill_name}</span>
                    <span className="text-gray-700">{entry.question_text}</span>
                  </li>
                ))}
              </ol>
            )}
          </section>

          {/* --- the pool, by skill ------------------------------------- */}
          {pool.groups.map((group) => {
            const target = summary.per_skill.find((s) => s.skill_id === group.skill_id);
            return (
              <section key={group.skill_id}>
                <SectionHeading
                  title={`${group.skill_name} (${group.questions.length})`}
                  hint={group.assessment_scope}
                  action={
                    <div className="flex items-center gap-2">
                      {group.priority === "high" && <Badge tone="brand" dot>high priority</Badge>}
                      {target && (
                        <span className={cn(
                          "text-xs",
                          target.have < target.min_items ? "font-medium text-error-600"
                            : target.have < target.target ? "text-warning-700"
                              : "text-gray-500",
                        )}>
                          {target.have} of {target.target} planned
                          {target.have < target.min_items && ` · needs ${target.min_items}`}
                        </span>
                      )}
                    </div>
                  }
                />
                <div className="space-y-2.5">
                  {group.questions.map((q) => (
                    <QuestionCard
                      key={q.question_id}
                      question={q}
                      pool={pool}
                      busy={busy === q.question_id}
                      onPatch={(patch) =>
                        run(q.question_id, () => adminApi.editQuestion(id, q.question_id, patch))}
                      onRegenerate={() =>
                        run(q.question_id, () => adminApi.regenerateQuestion(id, q.question_id))}
                      onRemove={() =>
                        run(q.question_id, () => adminApi.removeQuestion(id, q.question_id))}
                    />
                  ))}
                </div>
              </section>
            );
          })}

          {adding ? (
            <AddQuestion
              pool={pool}
              onCancel={() => setAdding(false)}
              onSave={async (body) => {
                await run("add", () => adminApi.addQuestion(id, body));
                setAdding(false);
              }}
            />
          ) : (
            <Button variant="secondary" onClick={() => setAdding(true)}>
              <Plus className="h-4 w-4" />
              Add a question
            </Button>
          )}

          <section className="card flex flex-wrap items-center justify-between gap-3 border-brand-200 p-5">
            <div>
              <p className="text-md font-semibold text-gray-900">
                Happy with the pool?
              </p>
              <p className="mt-0.5 max-w-[58ch] text-sm text-gray-600">
                Publishing freezes this assessment so candidates all sit the same one.
                It can't be edited afterwards — later edits become the next version.
              </p>
            </div>
            <Button
              onClick={() => navigate({ name: "publish", id })}
              disabled={!coverage.ok}
            >
              <Lock className="h-4 w-4" />
              Review &amp; publish
            </Button>
          </section>

          <p className="pb-2 text-xs text-gray-500">
            Changes save as you make them. Nothing here is published — candidates still
            sit the existing interview until this version is published.
          </p>
        </>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------- */

function Empty({ onGenerate }: { onGenerate: () => void }) {
  return (
    <div className="card grid place-items-center px-6 py-16 text-center">
      <div className="mb-3 grid h-9 w-9 place-items-center rounded-md border border-gray-200 bg-gray-50 text-gray-400">
        <ListChecks className="h-4 w-4" />
      </div>
      <p className="text-md font-semibold text-gray-900">No questions yet</p>
      <p className="mt-1 max-w-[54ch] text-sm text-gray-500">
        Tara writes the questions from the skills and tasks you approved — each one tied
        to a skill, grounded in a task, with the signals a good answer contains and the
        follow-ups to fall back on.
      </p>
      <div className="mt-4">
        <Button onClick={onGenerate}>
          <Sparkles className="h-4 w-4" />
          Generate questions
        </Button>
      </div>
    </div>
  );
}

function Generating({ role }: { role: string }) {
  return (
    <div className="card grid place-items-center px-6 py-20 text-center">
      <div className="brand-gradient grid h-10 w-10 animate-pulse place-items-center rounded-lg text-white">
        <Sparkles className="h-5 w-5" />
      </div>
      <p className="mt-4 text-md font-semibold text-gray-900">Writing the questions…</p>
      <p className="mt-1 max-w-[52ch] text-sm text-gray-500">
        Tara is working through the skills for {role || "this role"} one at a time,
        writing the questions, the signals to listen for, and the follow-ups.
      </p>
    </div>
  );
}

function QuestionCard({
  question, pool, busy, onPatch, onRegenerate, onRemove,
}: {
  question: PoolQuestion;
  pool: QuestionPool;
  busy: boolean;
  onPatch: (patch: QuestionPatchInput) => void;
  onRegenerate: () => void;
  onRemove: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(question.prompt);
  const [cues, setCues] = useState(question.looking_for.join("\n"));
  const [probes, setProbes] = useState(question.probe_bank.join("\n"));
  const [clarify, setClarify] = useState(question.clarify);

  useEffect(() => {
    setText(question.prompt);
    setCues(question.looking_for.join("\n"));
    setProbes(question.probe_bank.join("\n"));
    setClarify(question.clarify);
  }, [question]);

  const skills = pool.groups.map((g) => ({ id: g.skill_id, name: g.skill_name }));

  return (
    <div className={cn("card p-4", busy && "opacity-60")}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="neutral">{TYPE_LABEL[question.question_type]}</Badge>
          <DifficultyPill level={question.difficulty} />
          {question.task && (
            <span className="rounded-xs border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-xs text-gray-600">
              {question.task.name}
            </span>
          )}
          {question.source === "manual" && <Badge tone="brand">added by you</Badge>}
        </div>
        <div className="flex items-center gap-1">
          <button onClick={onRegenerate} title="Rewrite this one question"
                  className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700">
            <RefreshCw className="h-4 w-4" />
          </button>
          <button onClick={() => setEditing((e) => !e)} title="Edit"
                  className={cn("rounded p-1 hover:bg-gray-100",
                    editing ? "text-brand-600" : "text-gray-400 hover:text-gray-700")}>
            {editing ? <Check className="h-4 w-4" /> : <ListChecks className="h-4 w-4" />}
          </button>
          <button onClick={onRemove} title="Remove"
                  className="rounded p-1 text-gray-400 hover:bg-error-50 hover:text-error-600">
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>

      {editing ? (
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2}
                  onBlur={() => text !== question.prompt && onPatch({ question_text: text })}
                  className="mt-2 w-full rounded-md border border-gray-300 px-2 py-1.5 text-md text-gray-900 shadow-xs focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100" />
      ) : (
        <p className="mt-2 text-md leading-relaxed text-gray-900">{question.prompt}</p>
      )}

      {editing && (
        <div className="mt-2 flex flex-wrap gap-2">
          <Select label="Primary skill" value={question.primary_skill.id}
                  options={skills.map((s) => [s.id, s.name])}
                  onChange={(v) => onPatch({ primary_skill_id: v })} />
          <Select label="Type" value={question.question_type}
                  options={Object.entries(TYPE_LABEL)}
                  onChange={(v) => onPatch({ question_type: v as QuestionType })} />
          <Select label="Difficulty" value={question.difficulty}
                  options={[["easy", "Easy"], ["medium", "Medium"], ["hard", "Hard"]]}
                  onChange={(v) => onPatch({ difficulty: v as Difficulty })} />
        </div>
      )}

      <Detail title="What a good answer contains"
              hint="The runtime matches these against what the candidate actually says.">
        {editing ? (
          <textarea value={cues} onChange={(e) => setCues(e.target.value)} rows={4}
                    onBlur={() => onPatch({ looking_for: cues.split("\n").filter((c) => c.trim()) })}
                    className={editBox} />
        ) : (
          <ul className="ml-4 list-disc space-y-0.5 text-sm text-gray-700">
            {question.looking_for.map((c) => <li key={c}>{c}</li>)}
          </ul>
        )}
      </Detail>

      <Detail title="Evaluation criteria"
              hint="Written before the candidate answers, and frozen when this is published.">
        <ul className="space-y-1 text-sm">
          {question.evaluation_criteria.map((c) => (
            <li key={c.criterion_id} className="flex gap-2">
              <span className={cn(
                "mt-1 h-1.5 w-1.5 shrink-0 rounded-full",
                c.importance === "high" ? "bg-brand-500" : "bg-gray-300",
              )} />
              <span>
                <span className="font-medium text-gray-900">{c.label}</span>
                {c.description && <span className="text-gray-600"> — {c.description}</span>}
              </span>
            </li>
          ))}
        </ul>
      </Detail>

      <Detail title={`Fallback follow-ups (${question.probe_bank.length})`}
              hint="Used when a live follow-up is rejected by the guardrails.">
        {editing ? (
          <textarea value={probes} onChange={(e) => setProbes(e.target.value)} rows={3}
                    onBlur={() => onPatch({ probe_bank: probes.split("\n").filter((p) => p.trim()) })}
                    className={editBox} />
        ) : (
          <ul className="ml-4 list-disc space-y-0.5 text-sm text-gray-700">
            {question.probe_bank.map((p) => <li key={p}>{p}</li>)}
          </ul>
        )}
      </Detail>

      <Detail title="If the candidate asks what you mean"
              hint="Must not give away the signals above.">
        {editing ? (
          <textarea value={clarify} onChange={(e) => setClarify(e.target.value)} rows={2}
                    onBlur={() => clarify !== question.clarify && onPatch({ clarify })}
                    className={editBox} />
        ) : (
          <p className="text-sm italic text-gray-700">{question.clarify}</p>
        )}
      </Detail>
    </div>
  );
}

const editBox =
  "w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-xs focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100";

function Detail({
  title, hint, children,
}: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="mt-3 rounded-md border border-gray-200 bg-gray-25 p-2.5">
      <p className="label">{title}</p>
      {hint && <p className="mt-0.5 text-2xs text-gray-500">{hint}</p>}
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

function Select({
  label, value, options, onChange,
}: {
  label: string;
  value: string;
  options: [string, string][];
  onChange: (value: string) => void;
}) {
  return (
    <label className="text-xs text-gray-500">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}
              className="ml-1.5 h-7 rounded-md border border-gray-300 bg-surface px-1.5 text-sm text-gray-900 shadow-xs focus:border-brand-400 focus:outline-none">
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}

/**
 * Adding a question by hand asks for everything a generated one carries.
 * A bare question with no skill, no signals and no rubric is one the runtime
 * cannot classify and the scoring engine cannot judge — so it is not something
 * this system can accept, whoever wrote it.
 */
function AddQuestion({
  pool, onCancel, onSave,
}: {
  pool: QuestionPool;
  onCancel: () => void;
  onSave: (body: NewQuestionInput) => void;
}) {
  const [text, setText] = useState("");
  const [skill, setSkill] = useState(pool.groups[0]?.skill_id ?? "");
  const [type, setType] = useState<QuestionType>("situational");
  const [difficulty, setDifficulty] = useState<Difficulty>("medium");
  const [cues, setCues] = useState("");
  const [criteria, setCriteria] = useState("");
  const [probes, setProbes] = useState("");
  const [clarify, setClarify] = useState("");

  return (
    <div className="card space-y-3 border-brand-200 p-4">
      <div className="flex items-center justify-between">
        <p className="text-md font-semibold text-gray-900">Add a question</p>
        <button onClick={onCancel} className="rounded p-1 text-gray-400 hover:bg-gray-100">
          <X className="h-4 w-4" />
        </button>
      </div>

      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2}
                placeholder="The question, as it will be spoken aloud"
                className={editBox} />

      <div className="flex flex-wrap gap-2">
        <Select label="Skill" value={skill}
                options={pool.groups.map((g) => [g.skill_id, g.skill_name])}
                onChange={setSkill} />
        <Select label="Type" value={type} options={Object.entries(TYPE_LABEL)}
                onChange={(v) => setType(v as QuestionType)} />
        <Select label="Difficulty" value={difficulty}
                options={[["easy", "Easy"], ["medium", "Medium"], ["hard", "Hard"]]}
                onChange={(v) => setDifficulty(v as Difficulty)} />
      </div>

      <Field label="What a good answer contains" hint="One per line, three to five.">
        <textarea value={cues} onChange={(e) => setCues(e.target.value)} rows={4}
                  placeholder={"names the decision they made\nexplains why\nstates the outcome"}
                  className={editBox} />
      </Field>
      <Field label="Evaluation criteria" hint="One per line. At least two.">
        <textarea value={criteria} onChange={(e) => setCriteria(e.target.value)} rows={3}
                  placeholder={"Containment first\nCommunication"} className={editBox} />
      </Field>
      <Field label="Fallback follow-ups" hint="One per line. At least two, each a question.">
        <textarea value={probes} onChange={(e) => setProbes(e.target.value)} rows={3}
                  placeholder={"What made you choose that?\nHow did you know it worked?"}
                  className={editBox} />
      </Field>
      <Field label="If they ask what you mean" hint="Must not give away the signals.">
        <textarea value={clarify} onChange={(e) => setClarify(e.target.value)} rows={2}
                  className={editBox} />
      </Field>

      <div className="flex items-center gap-2 pt-1">
        <Button size="sm" onClick={() => onSave({
          question_text: text,
          primary_skill_id: skill,
          question_type: type,
          difficulty,
          looking_for: cues.split("\n").filter((c) => c.trim()),
          evaluation_criteria: criteria.split("\n").filter((c) => c.trim()).map((label) => ({
            label, description: "", importance: "medium" as Priority,
          })),
          probe_bank: probes.split("\n").filter((p) => p.trim()),
          clarify,
        })}>
          <Check className="h-4 w-4" />
          Add it
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
        <p className="flex items-center gap-1 text-xs text-gray-500">
          <AlertTriangle className="h-3.5 w-3.5 text-gray-400" />
          Checked the same way a generated question is.
        </p>
      </div>
    </div>
  );
}

function Field({
  label, hint, children,
}: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-gray-700">{label}</span>
      {hint && <span className="ml-1.5 text-xs text-gray-500">{hint}</span>}
      <div className="mt-1">{children}</div>
    </label>
  );
}
