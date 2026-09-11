import type { Interview, Skill } from "../lib/adminApi";
import { Section } from "./bits";
import { cn } from "../lib/cn";

/**
 * Step 2 — what the job achieves, and the work that achieves it.
 *
 * This screen is the argument for everything after it. A flat list of
 * competencies is hard to check and easy to wave through; "you will diagnose a
 * billing failure from a vague report, and that needs troubleshooting and
 * communication clarity" is something a hiring manager can confirm or correct
 * on sight.
 *
 * It's read-only on purpose. The recruiter corrects the *skills* — that's where
 * the decisions are. Editing task prose would be busywork that changes nothing
 * about the interview.
 */
export function OutcomesTasks({ iv }: { iv: Interview }) {
  if (!iv.extracted) return null;

  const byId = new Map(iv.skills.map((s) => [s.competency_id, s]));

  return (
    <Section
      title="What this role achieves, and the work behind it"
      hint="Inferred from the description. This is why the skills below were chosen — check it reads like the job you're actually hiring for."
    >
      <div className="mb-5">
        <p className="mb-2 text-xs font-bold uppercase tracking-wider text-gray-400">Outcomes</p>
        <ul className="space-y-1.5">
          {iv.outcomes.map((o) => (
            <li key={o} className="flex gap-2.5 text-[15px] leading-relaxed text-gray-700">
              <span className="mt-[9px] h-1.5 w-1.5 shrink-0 rounded-full bg-brand-400" />
              {o}
            </li>
          ))}
        </ul>
      </div>

      <div>
        <p className="mb-2 text-xs font-bold uppercase tracking-wider text-gray-400">
          Tasks &amp; the skills they need
        </p>
        <div className="space-y-2">
          {iv.tasks.map((task, i) => (
            <div key={i} className="rounded-lg border border-gray-200 p-3">
              <p className="text-sm leading-relaxed text-gray-800">{task.description}</p>
              {task.outcome && (
                <p className="mt-1 text-xs text-gray-400">drives: {task.outcome}</p>
              )}
              <div className="mt-2 flex flex-wrap gap-1.5">
                {task.required_skills.map((cid) => {
                  const skill: Skill | undefined = byId.get(cid);
                  if (!skill) return null;
                  return (
                    <span
                      key={cid}
                      className={cn(
                        "rounded-full border px-2 py-0.5 text-[11px] font-medium",
                        skill.evaluated
                          ? "border-brand-200 bg-brand-50 text-brand-700"
                          : "border-gray-200 bg-gray-50 text-gray-500",
                      )}
                      title={skill.evaluated ? "Interviewed" : "Not interviewed"}
                    >
                      {skill.name}
                    </span>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-gray-400">
          Highlighted tags are the skills this interview actually assesses.
        </p>
      </div>
    </Section>
  );
}
