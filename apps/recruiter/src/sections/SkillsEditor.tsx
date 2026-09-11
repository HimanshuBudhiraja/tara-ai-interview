import { useEffect, useState } from "react";
import { ChevronDown, Plus, RotateCcw, TriangleAlert, X } from "lucide-react";
import { adminApi, type Interview, type PoolPayload, type Skill, type SkillDomain } from "../lib/adminApi";
import { Section, PriorityPicker, Tick } from "./bits";
import { cn } from "../lib/cn";

/**
 * Step 3 — the skills, and the recruiter's edits on top of them.
 *
 * This is the trust story of the whole create flow: "TARA inferred it, I
 * adjusted it." Extraction without editing is an oracle you either accept or
 * abandon; extraction *with* editing is a first draft, which is what a
 * recruiter actually wants from it.
 *
 * Five things are editable per skill, and each maps to a real consequence:
 *
 *   evaluated  — is this interviewed at all
 *   domain     — which catalogued domain it belongs to
 *   priority   — how much of the interview it gets (a band, never a percentage)
 *   proficiency— the bar the role genuinely requires
 *   bank       — which authored questions can assess it
 *
 * The domain is the one field that is NOT free text, and the asymmetry is
 * deliberate. A skill's name stays in the job description's own words —
 * renaming "Retry-safe capture paths" to "Software Engineering" is how a
 * recruiter stops recognising their own role. The domain is drawn from the
 * skill master so that the same competency in two interviews can be counted
 * as the same thing. Tara proposes one from the skill's own words; the
 * recruiter corrects it, and a correction is never overwritten.
 *
 * The bank column is the honest part. This build has no Question Generation
 * Engine, so a skill with no bank behind it cannot be assessed — and rather than
 * hide that, the row says so and offers to remap or drop it.
 */
/** Technical first, then functional, then behavioural — the order a recruiter
 *  reading a role's skill list expects them in. */
const CATEGORY_ORDER = ["technical", "functional", "behavioural"] as const;

const CATEGORY_LABEL: Record<string, string> = {
  technical: "Technical",
  functional: "Functional",
  behavioural: "Behavioural",
};

export function SkillsEditor({
  iv,
  pool,
  onPatchSkill,
  onRemove,
  onAdd,
  onReset,
}: {
  iv: Interview;
  pool: PoolPayload;
  onPatchSkill: (competency_id: string, changes: Partial<Skill>) => void;
  onRemove: (competency_id: string) => void;
  onAdd: (name: string) => void;
  onReset: () => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const [newSkill, setNewSkill] = useState("");
  // Fetched once for the whole editor rather than per row: it is shipped
  // content, identical for every skill on the screen.
  const [domains, setDomains] = useState<SkillDomain[]>([]);

  useEffect(() => {
    let live = true;
    adminApi
      .skillDomains()
      .then((m) => live && setDomains(m.domains))
      .catch(() => undefined); // the picker degrades to the stored label
    return () => {
      live = false;
    };
  }, []);

  if (!iv.extracted) return null;

  const evaluated = iv.skills.filter((s) => s.evaluated);
  const rest = iv.skills.filter((s) => !s.evaluated);
  const uncovered = evaluated.filter((s) => !s.pool_competency);

  return (
    <Section
      title="Skills to evaluate"
      hint={`${evaluated.length} of ${iv.skills.length} skills are interviewed. TARA picked the highest-priority ones — change anything that doesn't match the role.`}
      action={
        <button
          onClick={onReset}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 hover:text-gray-700"
          title="Put priorities and proficiency levels back to what TARA inferred"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          Reset to inferred
        </button>
      }
    >
      {uncovered.length > 0 && (
        <div className="mb-4 rounded-lg border border-warning-200 bg-warning-25 px-3.5 py-3">
          <p className="flex gap-2 text-sm font-medium text-warning-700">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
            {uncovered.length} skill{uncovered.length > 1 ? "s have" : " has"} no questions behind
            {uncovered.length > 1 ? " them" : " it"}
          </p>
          <p className="mt-1 pl-6 text-sm leading-relaxed text-warning-700">
            These won't be assessed. Map each one to a question bank that covers it, or take it out
            of the evaluated set — leaving it in makes the interview look broader than it is.
          </p>
        </div>
      )}

      <div className="space-y-1.5">
        {evaluated.map((s) => (
          <SkillRow
            key={s.competency_id}
            skill={s}
            pool={pool}
            domains={domains}
            labels={iv.proficiency_labels}
            onPatch={(c) => onPatchSkill(s.competency_id, c)}
            onRemove={() => onRemove(s.competency_id)}
          />
        ))}
      </div>

      {rest.length > 0 && (
        <>
          <button
            onClick={() => setShowAll((v) => !v)}
            className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 hover:text-gray-700"
          >
            <ChevronDown className={cn("h-4 w-4 transition-transform", showAll && "rotate-180")} />
            {showAll ? "Hide" : "Show"} the {rest.length} skills not being interviewed
          </button>
          {showAll && (
            <div className="mt-2 space-y-1.5">
              {rest.map((s) => (
                <SkillRow
                  key={s.competency_id}
                  skill={s}
                  pool={pool}
                  domains={domains}
                  labels={iv.proficiency_labels}
                  onPatch={(c) => onPatchSkill(s.competency_id, c)}
                  onRemove={() => onRemove(s.competency_id)}
                />
              ))}
            </div>
          )}
        </>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-gray-100 pt-4">
        <input
          value={newSkill}
          onChange={(e) => setNewSkill(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && newSkill.trim()) {
              onAdd(newSkill.trim());
              setNewSkill("");
            }
          }}
          placeholder="Add a skill the description missed…"
          className="field min-w-[240px] flex-1"
        />
        <button
          onClick={() => {
            if (!newSkill.trim()) return;
            onAdd(newSkill.trim());
            setNewSkill("");
          }}
          disabled={!newSkill.trim()}
          className="inline-flex h-9 items-center gap-1.5 rounded-md border border-gray-300 bg-surface px-3 text-base font-semibold text-gray-700 shadow-xs transition-colors hover:bg-gray-50 disabled:opacity-45"
        >
          <Plus className="h-4 w-4" />
          Add
        </button>
      </div>
    </Section>
  );
}

function SkillRow({
  skill,
  pool,
  domains,
  labels,
  onPatch,
  onRemove,
}: {
  skill: Skill;
  pool: PoolPayload;
  domains: SkillDomain[];
  labels: string[];
  onPatch: (changes: Partial<Skill>) => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  const missingBank = skill.evaluated && !skill.pool_competency;
  // Only flagged on skills that are actually interviewed: an unfiled skill
  // nobody is assessing costs nothing, and publication does not block on it.
  const missingDomain = skill.evaluated && !skill.domain;

  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2.5",
        missingBank
          ? "border-warning-200 bg-warning-25"
          : skill.evaluated
            ? "border-gray-200 bg-surface"
            : "border-gray-100 bg-gray-50",
      )}
    >
      <div className="flex flex-wrap items-center gap-3">
        <Tick checked={skill.evaluated} onChange={(v) => onPatch({ evaluated: v })} />

        <div className="min-w-[160px] flex-1">
          <input
            value={skill.name}
            onChange={(e) => onPatch({ name: e.target.value })}
            className={cn(
              "w-full rounded-xs border-0 bg-transparent p-0 text-base font-medium focus:outline-none focus:ring-0",
              skill.evaluated ? "text-gray-900" : "text-gray-400",
            )}
          />
          <button
            onClick={() => setOpen((v) => !v)}
            className="mt-0.5 inline-flex items-center gap-0.5 text-[11px] font-medium text-gray-400 hover:text-gray-600"
          >
            {skill.tasks.length} task{skill.tasks.length === 1 ? "" : "s"} need this
            <ChevronDown className={cn("h-3 w-3 transition-transform", open && "rotate-180")} />
          </button>
        </div>

        <div className="shrink-0">
          <p className="label mb-1">Domain</p>
          <select
            value={skill.domain}
            onChange={(e) => onPatch({ domain: e.target.value })}
            aria-label={`Domain for ${skill.name}`}
            className={cn(
              "field-select min-w-[168px]",
              // An interviewed skill with no domain is the one state worth
              // colouring: it is what the publish check will stop on.
              missingDomain && "border-warning-300 bg-warning-25 text-warning-800",
            )}
          >
            <option value="">
              {missingDomain ? "Pick a domain…" : "No domain"}
            </option>
            {CATEGORY_ORDER.filter((c) => domains.some((d) => d.category === c)).map((c) => (
              <optgroup key={c} label={CATEGORY_LABEL[c] ?? c}>
                {domains
                  .filter((d) => d.category === c)
                  .map((d) => (
                    <option key={d.id} value={d.id} title={d.description}>
                      {d.label}
                    </option>
                  ))}
              </optgroup>
            ))}
            {/* A domain the catalogue no longer lists still has to render as
                something. Dropping it silently would look like the recruiter
                never chose one. */}
            {skill.domain && !domains.some((d) => d.id === skill.domain) && (
              <option value={skill.domain}>{skill.domain_label || skill.domain}</option>
            )}
          </select>
        </div>

        <div className="shrink-0">
          <p className="label mb-1">Priority</p>
          <PriorityPicker value={skill.priority} onChange={(p) => onPatch({ priority: p })} />
        </div>

        <div className="shrink-0">
          <p className="label mb-1">Required level</p>
          <select
            value={skill.proficiency_target}
            onChange={(e) => onPatch({ proficiency_target: Number(e.target.value) })}
            className="field-select"
          >
            {labels.map((l, i) => (
              <option key={l} value={i}>
                {l}
              </option>
            ))}
          </select>
        </div>

        <div className="shrink-0">
          <p className="label mb-1">Questions from</p>
          <select
            value={skill.pool_competency}
            onChange={(e) => onPatch({ pool_competency: e.target.value })}
            className={cn(
              "field-select",
              missingBank && "border-warning-400 text-warning-700",
            )}
          >
            <option value="">— no bank —</option>
            {pool.competencies.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label} ({c.items})
              </option>
            ))}
          </select>
        </div>

        <button
          onClick={onRemove}
          title="Remove this skill"
          className="shrink-0 rounded p-1 text-gray-300 hover:bg-gray-100 hover:text-gray-600"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {open && (
        <div className="mt-2.5 border-t border-gray-100 pt-2.5">
          {skill.tasks.length ? (
            <ul className="space-y-1">
              {skill.tasks.map((t) => (
                <li key={t} className="flex gap-2 text-xs leading-relaxed text-gray-500">
                  <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-gray-300" />
                  {t}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-gray-400">
              No task in the description needs this skill — worth asking whether it belongs.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
