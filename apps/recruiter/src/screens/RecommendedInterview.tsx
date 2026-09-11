import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  AlertTriangle, ArrowLeft, Check, ChevronDown, Clock, FileText, Globe,
  ListChecks, Plus, RefreshCw, Star, Target, Trash2,
} from "lucide-react";
import {
  adminApi, FormError, GenerationError,
  type Draft, type DraftPatch, type DesignSkill, type DesignTask,
  type InterviewType, type Priority, type SkillDomain,
} from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Badge, Button, Callout, Spinner } from "@tara/ui/primitives";
import { Block, Meta, Steps } from "../components/Wizard";
import { cn } from "../lib/cn";

/**
 * One review screen, not a wizard.
 *
 * Tara proposes; the recruiter corrects. Everything on this page is editable in
 * place, because making someone regenerate the whole interview to fix a typo in
 * one assessment scope is how a review screen becomes a thing people click past.
 *
 * Two ideas shape the layout:
 *
 * **Inferred is not the same as assessed.** The designer reads the JD and names
 * every skill it implies — fifteen or more for a senior role. All of them are
 * kept, because that list is the evidence the role was read properly. Only the
 * high-priority few are interviewed, because twenty skills in twenty minutes is
 * one shallow question each. So the assessed set is the page, and the rest sit
 * collapsed underneath it, one click from being added.
 *
 * **Tasks and skills are read together.** They are two halves of one mapping —
 * a task assesses skills, a skill is evidenced by tasks — so they sit side by
 * side rather than stacked a scroll apart.
 */
export function RecommendedInterview({ id }: { id: string }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The skill master, for the domain picker on each card. One fetch for the
  // screen: it is shipped content, identical for every skill on it.
  const [domains, setDomains] = useState<SkillDomain[]>([]);
  const [confirmRegen, setConfirmRegen] = useState(false);

  useEffect(() => {
    let live = true;
    adminApi
      .skillDomains()
      .then((m) => live && setDomains(m.domains))
      .catch(() => undefined); // the card falls back to the stored label
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    let live = true;
    adminApi.draft(id)
      .then((d) => live && setDraft(d))
      .catch((err: Error) => live && setError(err.message));
    return () => { live = false; };
  }, [id]);

  const patch = useCallback(
    async (body: DraftPatch) => {
      setBusy(true);
      setError(null);
      try {
        setDraft(await adminApi.editDraft(id, body));
      } catch (err) {
        // Server-side validation is the authoritative one, so its message is
        // what the recruiter sees — including the mapping rules that stop a
        // task being left assessing nothing.
        setError(err instanceof FormError
          ? Object.values(err.errors).join(" ")
          : (err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [id],
  );

  async function regenerate() {
    setConfirmRegen(false);
    setBusy(true);
    setError(null);
    try {
      setDraft(await adminApi.regenerate(id));
    } catch (err) {
      setError(err instanceof GenerationError ? err.message : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !draft) return <Callout tone="error" title="Couldn't open this interview">{error}</Callout>;
  if (!draft) return <div className="card grid place-items-center py-20"><Spinner /></div>;

  const assessed = draft.skills.filter((s) => s.evaluated);
  const inferred = draft.skills.filter((s) => !s.evaluated);
  // Flagged where it can still be fixed. Finding out at publication that six
  // skills need filing is finding out too late — but only the assessed ones
  // block anything, because an unassessed skill is a note, not a measurement.
  const undomained = assessed.filter((s) => !s.domain).length;

  return (
    <div className="max-w-6xl space-y-6">
      <Button variant="link" onClick={() => navigate({ name: "dashboard" })}>
        <ArrowLeft className="h-4 w-4" />
        All interviews
      </Button>

      <Steps current={2} />

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {/* No heading here: the shell header above already says what this
              screen is, and repeating it cost ~80px of the fold — which on
              this screen is the space where the skill/task mapping lives.
              The facts about the interview are what earns the room, as one
              line: reading them off four separate cards is how a recruiter
              ends up publishing a 40-minute deep interview they meant to be
              a prescreen. */}
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
            <Meta icon={<Globe className="h-3.5 w-3.5" />}>{draft.language_label}</Meta>
            <Meta icon={<Clock className="h-3.5 w-3.5" />}>
              {draft.experience_from}–{draft.experience_to} yrs experience
            </Meta>
            {draft.funnel_stage_label && (
              <Meta icon={<Target className="h-3.5 w-3.5" />}>{draft.funnel_stage_label}</Meta>
            )}
            <Meta icon={<Star className="h-3.5 w-3.5" />}>
              {assessed.length} of {draft.skills.length} skills assessed
            </Meta>
            <Meta icon={<ListChecks className="h-3.5 w-3.5" />}>{draft.tasks.length} tasks</Meta>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {busy && <Spinner className="text-gray-400" />}
          <Badge tone={draft.published_version ? "success" : "neutral"} dot>
            {draft.published_version ? `Published v${draft.published_version}` : "Draft"}
          </Badge>
          <Button variant="secondary" size="sm" onClick={() => setConfirmRegen(true)}>
            <RefreshCw className="h-4 w-4" />
            Regenerate
          </Button>
        </div>
      </header>

      {error && <Callout tone="error" title="That change wasn't saved">{error}</Callout>}

      {confirmRegen && (
        <Callout tone="warning" title="Regenerate from the original job description?">
          <p>
            This replaces the skills, tasks and assessment structure below — including
            anything you have edited. The job details you entered stay as they are, and
            nothing published changes.
          </p>
          <div className="mt-3 flex gap-2">
            <Button size="sm" variant="danger" onClick={regenerate}>Replace the recommendation</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmRegen(false)}>Keep what I have</Button>
          </div>
        </Callout>
      )}

      {draft.design_failed && (
        <Callout tone="error" title="This interview hasn't been designed yet">
          <p>
            The last attempt didn't complete. Your job details were kept — regenerate to
            try again.
          </p>
          <div className="mt-3">
            <Button size="sm" onClick={regenerate}>Try again</Button>
          </div>
        </Callout>
      )}

      {/* --- role + shape ------------------------------------------------- */}
      <Block
        title="Role and shape"
        hint={
          draft.funnel_stage_label
            ? `Your ${draft.funnel_stage_label.toLowerCase()} stage set the shape. Change the length here.`
            : "How long the interview runs."
        }
      >
        {/* Three facts on one row rather than three stacked sections. The
            shape is one decision with one consequence, and difficulty is not
            here at all: it follows the hiring stage, and a prescreen the
            recruiter has set to `hard` is not a prescreen. Nor is the
            duration a second control, because it was never independent of
            the type — a 20-minute "short" interview and a 20-minute "medium"
            one are the same interview under two labels. */}
        <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
          <div className="min-w-[240px] flex-1">
            <p className="label">Role title</p>
            <EditableText
              value={draft.role_title}
              onSave={(title) => patch({ title })}
              className="mt-1 text-md font-semibold tracking-tight text-gray-900"
            />
          </div>
          <Segmented
            label="Interview type"
            value={draft.assessment.interview_type}
            options={INTERVIEW_TYPES}
            onChange={(v) => patch({ interview_type: v as InterviewType })}
          />
          <div>
            <p className="label">Runs for</p>
            <p className="mt-2 flex items-baseline gap-1.5">
              <span className="tabular text-md font-semibold text-gray-900">
                {draft.assessment.recommended_duration_min} min
              </span>
              <span className="text-xs text-gray-500">
                set by the {draft.assessment.interview_type} type
              </span>
            </p>
          </div>
        </div>

        {draft.rationale && (
          <p className="border-t border-gray-100 pt-3 text-sm leading-relaxed text-gray-600">
            <span className="label mr-1.5 inline">Why</span>
            {draft.rationale}
          </p>
        )}
      </Block>

      {/* --- skills beside tasks -------------------------------------------
          Two halves of one mapping: a task assesses skills, a skill is
          evidenced by tasks. They have to be on screen together, because
          checking the mapping is the whole reason to read them — so each
          column scrolls inside its own pane rather than the page growing to
          the length of the longer list and pushing tasks off the bottom. */}
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Block
          title={`Skills assessed (${assessed.length})`}
          hint="What the interview measures. Questions are written for these."
          action={
            undomained > 0 ? (
              <span className="rounded-md border border-warning-200 bg-warning-25 px-2 py-1 text-xs font-medium text-warning-800">
                {undomained} without a domain
              </span>
            ) : undefined
          }
        >
          <Pane>
            {assessed.map((skill) => (
              <SkillCard
                key={skill.id}
                skill={skill}
                domains={domains}
                tasks={draft.tasks}
                onPatch={(fields) => patch({ skills: [{ id: skill.id, ...fields }] })}
                onRemove={() => patch({ remove_skills: [skill.id] })}
              />
            ))}
            {assessed.length === 0 && (
              <p className="flex items-center gap-1.5 text-sm font-medium text-error-600">
                <AlertTriangle className="h-4 w-4" />
                Nothing is being assessed. Add a skill from the inferred list below.
              </p>
            )}

            {/* The rest of what the JD implied. Inside the pane, at the end of
                the list, because it is the tail of the same list — not a
                separate section competing with the tasks beside it. */}
            <InferredSkills
              skills={inferred}
              onAssess={(skillId) => patch({ skills: [{ id: skillId, evaluated: true }] })}
              onRemove={(skillId) => patch({ remove_skills: [skillId] })}
            />
          </Pane>
        </Block>

        <Block
          title={`Tasks (${draft.tasks.length})`}
          hint="The work behind the skills. Each one names what it assesses."
        >
          <Pane>
            {draft.tasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                skills={draft.skills}
                onPatch={(fields) => patch({ tasks: [{ id: task.id, ...fields }] })}
                onRemove={() => patch({ remove_tasks: [task.id] })}
              />
            ))}
            {draft.tasks.length === 0 && (
              <p className="text-sm text-gray-500">No tasks. Regenerate to propose some.</p>
            )}
          </Pane>
        </Block>
      </div>

      {/* --- questions ----------------------------------------------------- */}
      <Block
        title={`Questions (${draft.question_count})`}
        hint={
          draft.questions_generated
            ? "Written from the skills and tasks above — each tied to a skill and grounded in a task. Open the pool to read or change any of them."
            : "Tara couldn't write the questions on this pass. The design is saved; generating again picks up from it."
        }
        action={
          <Button
            variant={draft.questions_generated ? "secondary" : "primary"}
            size="sm"
            onClick={() => navigate({ name: "question-pool", id })}
          >
            <ListChecks className="h-4 w-4" />
            {draft.questions_generated ? "Review questions" : "Generate questions"}
          </Button>
        }
      >
        {draft.questions_generated ? (
          <div className="flex flex-wrap gap-1.5">
            {assessed.map((skill) => {
              const n = draft.questions_by_skill[skill.id] ?? 0;
              return (
                <span
                  key={skill.id}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-sm",
                    // A skill with no questions cannot be assessed, and finding
                    // that out at publication is finding out too late. This is
                    // also how a skill added to the assessed set after
                    // generation shows up — it has none yet.
                    n === 0
                      ? "border-warning-200 bg-warning-25 text-warning-800"
                      : "border-gray-200 bg-surface text-gray-700",
                  )}
                >
                  {skill.name}
                  <span className="tabular-nums font-semibold">{n}</span>
                </span>
              );
            })}
          </div>
        ) : (
          <p className="text-sm text-gray-500">
            Nothing was saved, so nothing was lost — the skills and tasks above are
            exactly as they were.
          </p>
        )}
      </Block>

      {/* --- the source ---------------------------------------------------
          Below the work, not above it. Everything on this screen claims to be
          derived from the JD, so it has to be here — but it is reference, and
          sitting above the skills it pushed the mapping off the fold. */}
      {draft.job?.description && (
        <JobDescription
          description={draft.job.description}
          additional={draft.job.additional_information}
        />
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-gray-200 pt-5">
        <Button onClick={() => navigate({ name: "publish", id })}>
          Publish and invite
        </Button>
        <p className="max-w-[58ch] text-sm text-gray-500">
          Changes save as you make them. Publishing freezes this version — candidates
          can only ever sit a published one.
        </p>
      </div>
    </div>
  );
}

/**
 * A column that scrolls inside itself.
 *
 * Skills and tasks are read against each other, so both have to be on screen
 * at once. Left to the page, the taller of the two columns sets the page
 * height and the shorter one ends halfway up it — you scroll past eight skill
 * cards to reach the task whose mapping you were checking. Bounding each pane
 * keeps the two lists beside each other at every scroll position, and keeps
 * the block headings (and the "N without a domain" warning) in place while
 * you move through the list.
 */
function Pane({ children }: { children: ReactNode }) {
  return (
    <div className="max-h-[62vh] min-h-[280px] space-y-2.5 overflow-y-auto pr-1">
      {children}
    </div>
  );
}

const INTERVIEW_TYPES = ["short", "medium", "deep"];

/**
 * The job description, verbatim, collapsed.
 *
 * Present because everything on this screen claims to be derived from it, and
 * a recruiter checking whether Tara read the role properly should not have to
 * navigate away to see what it read. Collapsed because it is reference, not
 * the work: the skills below are what needs attention.
 */
function JobDescription({
  description,
  additional,
}: {
  description: string;
  additional: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="card p-5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-start justify-between gap-3 text-left"
      >
        <span>
          <span className="flex items-center gap-1.5 text-md font-semibold text-gray-900">
            <FileText className="h-4 w-4 text-gray-400" />
            Job description
          </span>
          <span className="mt-0.5 block text-sm text-gray-500">
            Exactly what was pasted in. Everything above was derived from this.
          </span>
        </span>
        <ChevronDown
          className={cn("mt-1 h-4 w-4 shrink-0 text-gray-400 transition-transform", open && "rotate-180")}
        />
      </button>
      {open && (
        <div className="mt-4 space-y-4 border-t border-gray-100 pt-4">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-700">
            {description}
          </p>
          {additional && (
            <div className="rounded-md border border-gray-200 bg-gray-25 p-3">
              <p className="label">Additional context</p>
              <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-gray-700">
                {additional}
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

/**
 * What the JD implied but the interview is not measuring.
 *
 * Collapsed by default and deliberately compact: these are not decisions
 * waiting to be made, they are the rest of the designer's reading. Kept
 * visible at all because a recruiter who disagrees with the cut needs
 * somewhere to disagree from, and because deleting a skill outright is a
 * different act from choosing not to interview it.
 */
function InferredSkills({
  skills,
  onAssess,
  onRemove,
}: {
  skills: DesignSkill[];
  onAssess: (skillId: string) => void;
  onRemove: (skillId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (skills.length === 0) return null;

  return (
    <div className="rounded-md border border-gray-200 bg-gray-25">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left"
      >
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-gray-800">
            Also inferred from the job description ({skills.length})
          </span>
          <span className="mt-0.5 block text-xs text-gray-500">
            Named by Tara, not being assessed. Add any of them to the interview.
          </span>
        </span>
        <ChevronDown
          className={cn("h-4 w-4 shrink-0 text-gray-400 transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <ul className="border-t border-gray-200">
          {skills.map((skill) => (
            <li
              key={skill.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-gray-200 px-3 py-2 last:border-0"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-gray-800">
                  {skill.name}
                </span>
                {skill.domain_label && (
                  <span className="mt-0.5 block truncate text-xs text-gray-500">
                    {skill.domain_label}
                  </span>
                )}
              </span>
              <PriorityDot priority={skill.priority} />
              <Button size="sm" variant="secondary" onClick={() => onAssess(skill.id)}>
                <Plus className="h-3.5 w-3.5" />
                Assess
              </Button>
              <RemoveButton label={skill.name} onRemove={() => onRemove(skill.id)} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------- */

function SkillCard({
  skill, domains, tasks, onPatch, onRemove,
}: {
  skill: DesignSkill;
  domains: SkillDomain[];
  tasks: DesignTask[];
  onPatch: (fields: Partial<Omit<DesignSkill, "id">>) => void;
  onRemove: () => void;
}) {
  // The same mapping the task cards show, read from this end. Without it the
  // relationship is only visible from the task side, so a skill that no task
  // evidences — assessed, but with nothing concrete behind the questions —
  // looks exactly like a skill that three tasks cover.
  const evidencedBy = tasks.filter((t) => t.skills_assessed.some((s) => s.id === skill.id));
  return (
    <div className={cn("card p-4", skill.priority === "high" && "border-l-2 border-l-brand-400")}>
      <div className="flex items-start gap-2">
        <EditableText
          value={skill.name}
          onSave={(name) => onPatch({ name })}
          className="min-w-0 flex-1 text-md font-semibold text-gray-900"
        />
        {/* One action per card. A second control for "keep it on the record
            but don't interview it" was a distinction the recruiter had not
            asked for, sitting on every row and competing with the delete. */}
        <RemoveButton label={skill.name} onRemove={onRemove} />
      </div>

      <EditableText
        value={skill.description}
        multiline
        placeholder="What this skill means for this role"
        onSave={(description) => onPatch({ description })}
        className="mt-1 text-sm text-gray-600"
      />

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div>
          <p className="label">Priority</p>
          <Segmented
            value={skill.priority}
            options={PRIORITIES}
            size="sm"
            onChange={(priority) => onPatch({ priority: priority as Priority })}
          />
        </div>
        <div>
          <p className="label">Required proficiency</p>
          <ProficiencyMeter
            value={skill.proficiency_target}
            onChange={(proficiency_target) => onPatch({ proficiency_target })}
          />
        </div>
      </div>

      <div className="mt-3 rounded-md border border-gray-200 bg-gray-25 p-2.5">
        <p className="label">Assessment scope / niche limitation</p>
        <EditableText
          value={skill.assessment_scope}
          multiline
          placeholder="What specifically gets probed within this skill"
          onSave={(assessment_scope) => onPatch({ assessment_scope })}
          className="mt-1 text-sm leading-relaxed text-gray-700"
        />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1.5 border-t border-gray-100 pt-3">
        <p className="label shrink-0">Domain</p>
        <DomainPicker
          skill={skill}
          domains={domains}
          onChange={(domain) => onPatch({ domain })}
        />
        {!skill.domain && (
          <span className="text-xs text-warning-700">
            Needed before this interview can be published
          </span>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
        <p className="label shrink-0">Evidenced by</p>
        {evidencedBy.length > 0 ? (
          evidencedBy.map((task) => (
            <span
              key={task.id}
              className="rounded-xs border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-xs text-gray-700"
            >
              {task.name}
            </span>
          ))
        ) : (
          <span className="flex items-center gap-1 text-xs font-medium text-warning-700">
            <AlertTriangle className="h-3.5 w-3.5" />
            No task covers this skill
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * The skill master, as a quiet chip rather than a form control.
 *
 * The domain is catalogued on purpose — a free-text domain aggregates with
 * nothing — so a picker is the honest control here; 22 domains is not a set
 * you type from memory. What it should not look like is a form field: the
 * name beside it is the editable part, and a full-height bordered select next
 * to it read as the more important of the two.
 */
function DomainPicker({
  skill, domains, onChange,
}: {
  skill: DesignSkill;
  domains: SkillDomain[];
  onChange: (domain: string) => void;
}) {
  const missing = !skill.domain;
  return (
    <span className="relative inline-flex items-center">
      <select
        value={skill.domain}
        onChange={(e) => onChange(e.target.value)}
        aria-label={`Domain for ${skill.name}`}
        className={cn(
          "h-7 max-w-[240px] cursor-pointer appearance-none truncate rounded-md border px-2 pr-6",
          "text-xs font-medium transition-colors focus:border-brand-400 focus:shadow-focus focus:outline-none",
          missing
            ? "border-warning-300 bg-warning-25 text-warning-800"
            : "border-transparent bg-gray-100 text-gray-700 hover:bg-gray-200",
        )}
      >
        <option value="">{missing ? "Pick a domain…" : "No domain"}</option>
        {DOMAIN_CATEGORIES.filter((c) => domains.some((d) => d.category === c)).map((c) => (
          <optgroup key={c} label={DOMAIN_CATEGORY_LABEL[c] ?? c}>
            {domains
              .filter((d) => d.category === c)
              .map((d) => (
                <option key={d.id} value={d.id} title={d.description}>
                  {d.label}
                </option>
              ))}
          </optgroup>
        ))}
        {/* A domain the catalogue no longer lists still renders as something,
            rather than silently looking like nobody ever chose one. */}
        {skill.domain && !domains.some((d) => d.id === skill.domain) && (
          <option value={skill.domain}>{skill.domain_label || skill.domain}</option>
        )}
      </select>
      <ChevronDown
        className={cn(
          "pointer-events-none absolute right-1.5 h-3.5 w-3.5",
          missing ? "text-warning-600" : "text-gray-400",
        )}
      />
    </span>
  );
}

/** Technical first, then functional, then behavioural. */
const DOMAIN_CATEGORIES = ["technical", "functional", "behavioural"] as const;

const DOMAIN_CATEGORY_LABEL: Record<string, string> = {
  technical: "Technical",
  functional: "Functional",
  behavioural: "Behavioural",
};

function TaskCard({
  task, skills, onPatch, onRemove,
}: {
  task: DesignTask;
  skills: DesignSkill[];
  onPatch: (fields: { name?: string; description?: string; priority?: Priority; skills_assessed?: string[] }) => void;
  onRemove: () => void;
}) {
  const mapped = new Set(task.skills_assessed.map((s) => s.id));

  return (
    <div className="card p-4">
      <div className="flex items-start gap-2">
        <EditableText
          value={task.name}
          onSave={(name) => onPatch({ name })}
          className="min-w-0 flex-1 text-md font-semibold text-gray-900"
        />
        <RemoveButton label={task.name} onRemove={onRemove} />
      </div>

      <EditableText
        value={task.description}
        multiline
        onSave={(description) => onPatch({ description })}
        className="mt-1 text-sm leading-relaxed text-gray-600"
      />

      <div className="mt-3">
        <p className="label">Priority</p>
        <Segmented
          value={task.priority}
          options={PRIORITIES}
          size="sm"
          onChange={(priority) => onPatch({ priority: priority as Priority })}
        />
      </div>

      <div className="mt-3 border-t border-gray-100 pt-3">
        <p className="label">Skills assessed</p>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {skills.map((skill) => {
            const on = mapped.has(skill.id);
            return (
              <button
                key={skill.id}
                type="button"
                onClick={() => {
                  const next = on
                    ? task.skills_assessed.filter((s) => s.id !== skill.id).map((s) => s.id)
                    : [...task.skills_assessed.map((s) => s.id), skill.id];
                  onPatch({ skills_assessed: next });
                }}
                // A task may map to a skill the interview is not assessing —
                // that is how the mapping stays true to the role rather than
                // to this one interview — so it is shown, dimmed, rather than
                // hidden and silently unmappable.
                title={skill.evaluated ? undefined : `${skill.name} is not being assessed`}
                className={cn(
                  "rounded-md border px-2 py-1 text-sm transition-colors",
                  on
                    ? "border-brand-300 bg-brand-50 font-medium text-brand-700"
                    : "border-gray-200 bg-surface text-gray-500 hover:border-gray-300",
                  !skill.evaluated && !on && "opacity-60",
                )}
              >
                {on && <Check className="mr-1 inline h-3 w-3" />}
                {skill.name}
              </button>
            );
          })}
        </div>
        {task.skills_assessed.length === 0 && (
          <p className="mt-1.5 flex items-center gap-1 text-xs font-medium text-error-600">
            <AlertTriangle className="h-3.5 w-3.5" />
            A task has to assess at least one skill.
          </p>
        )}
      </div>
    </div>
  );
}

const PRIORITIES = ["high", "medium", "low"];

/** 0 → 4, in the words the bands are defined in. */
const PROFICIENCY = [
  "Novice",
  "Advanced beginner",
  "Competent",
  "Proficient",
  "Expert",
];

/**
 * Required proficiency, as a meter you can click.
 *
 * A 0-4 integer is a level, not a choice from a list, and a five-item dropdown
 * hid both the scale and where this skill sat on it. Filled bars show the
 * position at a glance; the name spells out which level that is, because
 * "3" means nothing without it.
 */
function ProficiencyMeter({
  value, onChange,
}: {
  value: number;
  onChange: (level: number) => void;
}) {
  const level = Math.max(0, Math.min(4, value));
  return (
    <div className="mt-1.5 flex items-center gap-2">
      <span className="flex gap-0.5" role="group" aria-label="Required proficiency">
        {PROFICIENCY.map((name, i) => (
          <button
            key={name}
            type="button"
            onClick={() => onChange(i)}
            title={name}
            aria-label={name}
            aria-pressed={i === level}
            className={cn(
              "h-4 w-4 rounded-xs border transition-colors",
              i <= level
                ? "border-brand-500 bg-brand-500"
                : "border-gray-200 bg-gray-100 hover:border-gray-400",
            )}
          />
        ))}
      </span>
      <span className="text-xs font-medium text-gray-600">{PROFICIENCY[level]}</span>
    </div>
  );
}

/** Priority where there is room for a dot but not a control. */
function PriorityDot({ priority }: { priority: Priority }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 text-xs capitalize text-gray-500">
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          priority === "high" ? "bg-brand-500" : priority === "medium" ? "bg-gray-400" : "bg-gray-300",
        )}
      />
      {priority}
    </span>
  );
}

function RemoveButton({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <button
      type="button"
      onClick={onRemove}
      aria-label={`Remove ${label}`}
      title={`Remove ${label}`}
      className="shrink-0 rounded-md p-1.5 text-gray-400 transition-colors hover:bg-error-50 hover:text-error-600"
    >
      <Trash2 className="h-4 w-4" />
    </button>
  );
}

/**
 * A small set of mutually exclusive options, all of them visible.
 *
 * This was a dropdown. Three options behind a click is a click that buys
 * nothing, and it hid the one shape decision on this screen — whether this is
 * a short, medium or deep interview — inside a closed control.
 */
function Segmented({
  label, value, options, onChange, hint, size = "md",
}: {
  label?: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  hint?: string;
  size?: "sm" | "md";
}) {
  return (
    <div>
      {label && <p className="label">{label}</p>}
      <div
        role="group"
        aria-label={label}
        className="mt-1.5 inline-flex rounded-md border border-gray-300 bg-gray-50 p-0.5 shadow-xs"
      >
        {options.map((option) => {
          const on = option === value;
          return (
            <button
              key={option}
              type="button"
              onClick={() => !on && onChange(option)}
              aria-pressed={on}
              className={cn(
                "rounded-[5px] font-medium capitalize transition-colors",
                size === "sm" ? "px-2 py-0.5 text-xs" : "px-3 py-1 text-sm",
                on
                  ? "bg-surface text-gray-900 shadow-xs"
                  : "text-gray-500 hover:text-gray-900",
              )}
            >
              {option}
            </button>
          );
        })}
      </div>
      {hint && <p className="mt-1.5 text-xs text-gray-500">{hint}</p>}
    </div>
  );
}

/**
 * Click-to-edit text.
 *
 * Saves on blur rather than behind a Save button, because a review screen with
 * twenty Save buttons is a review screen nobody finishes. Bordered only on
 * hover and focus: a page where every line of copy sits in its own visible box
 * reads as a form to fill in, when what it is is a recommendation to read —
 * the editing is available, not the point.
 */
function EditableText({
  value, onSave, className, multiline, placeholder,
}: {
  value: string;
  onSave: (value: string) => void;
  className?: string;
  multiline?: boolean;
  placeholder?: string;
}) {
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);

  const shared = {
    value: local,
    placeholder,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setLocal(e.target.value),
    onBlur: () => local !== value && onSave(local),
    className: cn(
      className,
      "block w-full resize-y rounded-md border border-transparent bg-transparent px-2 py-1",
      "transition-colors placeholder:font-normal placeholder:text-gray-400",
      "hover:border-gray-200 hover:bg-gray-25",
      "focus:border-brand-400 focus:bg-surface focus:shadow-focus focus:outline-none",
    ),
  };
  return multiline ? <textarea rows={2} {...shared} /> : <input {...shared} />;
}
