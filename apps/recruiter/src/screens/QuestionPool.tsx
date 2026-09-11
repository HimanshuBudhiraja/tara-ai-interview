import { useEffect, useState } from "react";
import { adminApi, type PoolPayload } from "../lib/adminApi";
import { Callout } from "@tara/ui/primitives";
import { DifficultyPill } from "../components/RecruiterShell";

/**
 * The authored pool, read-only.
 *
 * Read-only because this build has no Question Generation Engine and no rubric
 * editor — questions are authored in `customer_support_rep_pool.json` and
 * reviewed by a human before they ship. An edit form here would imply questions
 * can be changed mid-flight by whoever is logged in, which is exactly the
 * governance problem the authored pool exists to avoid.
 *
 * What a recruiter needs from this screen is confidence: see every question
 * that could be asked, what a strong answer looks like, and the exact wording
 * of every follow-up Tara can fall back to.
 */
export function QuestionPool() {
  const [pool, setPool] = useState<PoolPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminApi.pool().then(setPool).catch((e) => setError(e.message));
  }, []);

  if (error) return <Callout tone="error">{error}</Callout>;
  if (!pool) return <div className="card h-64 animate-pulse bg-gray-50" />;

  return (
    <div className="space-y-5">
      <Callout tone="info" title="Authored, not generated">
        Every question here was written and reviewed by a person. Tara chooses which of them to ask
        and may follow up on an answer, but she never invents a question. Editing happens in the
        pool file, under review — not from this screen.
      </Callout>

      {pool.competencies.map((c) => {
        const items = pool.items.filter((i) => i.competency === c.id);
        return (
          <section key={c.id}>
            <div className="mb-2.5 flex items-baseline gap-2.5">
              <h2 className="text-md font-semibold text-gray-900">{c.label}</h2>
              <span className="meta">
                {items.length} question{items.length === 1 ? "" : "s"} · default weight{" "}
                {Math.round(c.weight * 100)}%
              </span>
            </div>
            <div className="space-y-2.5">
              {items.map((item) => (
                <article key={item.id} className="card p-4">
                  <div className="mb-2 flex items-center gap-2">
                    <DifficultyPill level={item.difficulty} />
                    <span className="meta font-mono">{item.id}</span>
                    <span className="meta">· ~{Math.round(item.time_estimate_sec / 60)} min</span>
                  </div>
                  <p className="text-base font-medium leading-relaxed text-gray-900">
                    {item.prompt}
                  </p>

                  <div className="mt-4 grid gap-4 border-t border-gray-100 pt-4 sm:grid-cols-2">
                    <Block label="What a strong answer shows">
                      <ul className="list-inside list-disc space-y-1">
                        {item.looking_for.map((cue) => (
                          <li key={cue}>{cue}</li>
                        ))}
                      </ul>
                    </Block>
                    <Block label="Authored follow-ups">
                      <ul className="list-inside list-disc space-y-1">
                        {item.probe_bank.map((p) => (
                          <li key={p}>{p}</li>
                        ))}
                      </ul>
                    </Block>
                  </div>
                  <div className="mt-3 border-t border-gray-100 pt-3">
                    <Block label="If the candidate asks what it means">{item.clarify}</Block>
                  </div>
                </article>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="label mb-1">{label}</p>
      <div className="text-sm leading-relaxed text-gray-600">{children}</div>
    </div>
  );
}
