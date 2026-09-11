import { useEffect, useState } from "react";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { adminApi, type Interview } from "../lib/adminApi";
import { href, navigate, type InterviewTab } from "../lib/route";
import { Badge, Button } from "@tara/ui/primitives";
import { StatusPill } from "../components/RecruiterShell";
import { InterviewBuilder } from "./InterviewBuilder";
import { InterviewCandidates } from "./InterviewCandidates";
import { InterviewResults } from "./InterviewResults";
import { FairnessView } from "./FairnessView";
import { cn } from "../lib/cn";

/**
 * An interview is a container, not a page.
 *
 * Before this, "candidates" was a single global list and results didn't exist,
 * so a recruiter running three roles at once had no way to look at one of them.
 * Everything scoped to an interview now lives under it: how it's configured, who
 * was invited to it, how they did, and what the fairness audit says about it.
 */
const TABS: { id: InterviewTab; label: string }[] = [
  { id: "configure", label: "Configure" },
  { id: "candidates", label: "Candidates" },
  { id: "results", label: "Results" },
  { id: "fairness", label: "Fairness" },
];

export function InterviewWorkspace({ id, tab }: { id: string; tab: InterviewTab }) {
  const [iv, setIv] = useState<Interview | null>(null);

  useEffect(() => {
    let live = true;
    adminApi
      .interview(id)
      .then((data) => live && setIv(data))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [id, tab]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button
          onClick={() => navigate({ name: "interviews" })}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 transition-colors hover:text-gray-900"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          All interviews
        </button>

        {iv && (
          <div className="flex items-center gap-2">
            <StatusPill status={iv.status} />
            {iv.candidates.total > 0 && (
              <Badge tone="neutral">
                {iv.candidates.total} invited · {iv.candidates.complete} complete
              </Badge>
            )}
            {iv.status === "published" && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => navigate({ name: "interview", id, tab: "candidates" })}
              >
                Invite
              </Button>
            )}
          </div>
        )}
      </div>

      {/* Tabs. Real links, so a recruiter can bookmark "results for this role". */}
      <div className="flex gap-0.5 border-b border-gray-200">
        {TABS.map((t) => {
          const active = t.id === tab;
          return (
            <a
              key={t.id}
              href={href({ name: "interview", id, tab: t.id })}
              onClick={(e) => {
                e.preventDefault();
                navigate({ name: "interview", id, tab: t.id });
              }}
              className={cn(
                "-mb-px border-b-2 px-3 py-2 text-sm font-semibold transition-colors",
                active
                  ? "border-brand-500 text-gray-900"
                  : "border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700",
              )}
            >
              {t.label}
            </a>
          );
        })}
        {iv?.status === "published" && (
          <a
            href="/?invite=demo"
            target="_blank"
            rel="noreferrer"
            className="ml-auto inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gray-400 transition-colors hover:text-gray-700"
            title="Open the candidate experience in a new tab"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Candidate view
          </a>
        )}
      </div>

      {tab === "configure" && <InterviewBuilder id={id} />}
      {tab === "candidates" && <InterviewCandidates interviewId={id} />}
      {tab === "results" && <InterviewResults interviewId={id} />}
      {tab === "fairness" && <FairnessView interviewId={id} />}
    </div>
  );
}
