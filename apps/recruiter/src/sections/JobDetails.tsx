import { useRef, useState } from "react";
import { FileText, Sparkles, Upload } from "lucide-react";
import type { ExtractInput, Interview } from "../lib/adminApi";
import { Button, Callout } from "@tara/ui/primitives";
import { Section, Field } from "./bits";

/**
 * Step 1 — the job, and the description everything else is inferred from.
 *
 * The JD is the input, not the competency list. A recruiter has the JD already;
 * asking them to translate it into competencies by hand is the work we're
 * supposed to be doing for them.
 *
 * Experience band matters more than it looks: it sets the proficiency bar. The
 * same competencies at "2-4 years" and at "10+ years" are very different
 * interviews, and without it every role drifts towards an unpassable profile.
 */
export function JobDetails({
  iv,
  onPatch,
  onExtract,
  extracting,
  error,
}: {
  iv: Interview;
  onPatch: (changes: Record<string, unknown>) => void;
  onExtract: (input: ExtractInput) => void;
  extracting: boolean;
  error: string | null;
}) {
  const [jd, setJd] = useState(iv.jd_text);
  const [fileNote, setFileNote] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const readFile = async (file: File) => {
    const isText = /\.(txt|md|markdown|text)$/i.test(file.name) || file.type.startsWith("text/");
    if (!isText) {
      // Parsing .docx/.pdf in the browser needs a library and gets the layout
      // wrong often enough to matter. Pasting takes five seconds and is exact.
      setFileNote(
        `${file.name} isn't a plain-text file. Open it, copy the text, and paste it below — that way you can see exactly what's being analysed.`,
      );
      return;
    }
    const text = await file.text();
    setJd(text);
    setFileNote(`Loaded ${file.name} (${Math.round(text.length / 1000)}k characters).`);
  };

  const canExtract = jd.trim().length > 120 && !extracting;

  return (
    <>
      <Section title="The job" hint="What the candidate is being interviewed for.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Job profile name" hint="Shown to the candidate.">
            <input
              value={iv.role_title}
              onChange={(e) => onPatch({ role_title: e.target.value })}
              placeholder="Customer Support Representative"
              className="field"
            />
          </Field>
          <Field label="Interview title" hint="Internal — candidates never see it.">
            <input
              value={iv.title}
              onChange={(e) => onPatch({ title: e.target.value })}
              className="field"
            />
          </Field>
          <Field label="Company" hint="Context TARA speaks with.">
            <input
              value={iv.company.name}
              onChange={(e) => onPatch({ company_name: e.target.value })}
              placeholder="Northwind"
              className="field"
            />
          </Field>
          <Field
            label="Experience expected"
            hint="Sets the proficiency bar — the same skills at 2 years and 10 years are different interviews."
          >
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                max={50}
                value={iv.experience_from}
                onChange={(e) => onPatch({ experience_from: Number(e.target.value) })}
                className="field tabular w-[70px]"
              />
              <span className="text-sm text-gray-400">to</span>
              <input
                type="number"
                min={0}
                max={50}
                value={iv.experience_to}
                onChange={(e) => onPatch({ experience_to: Number(e.target.value) })}
                className="field tabular w-[70px]"
              />
              <span className="text-sm text-gray-400">years</span>
            </div>
          </Field>
        </div>

        <div className="mt-4">
          <Field
            label="What the company does"
            hint="Optional. Grounds the analysis and gives TARA context if a candidate asks."
          >
            <textarea
              value={iv.company.about}
              onChange={(e) => onPatch({ company_about: e.target.value })}
              rows={2}
              placeholder="B2B SaaS billing platform for mid-market finance teams."
              className="field-textarea resize-none"
            />
          </Field>
        </div>
      </Section>

      <Section
        title="Job description"
        hint="Everything below — outcomes, tasks, and the skills to assess — is inferred from this."
        action={
          <button
            onClick={() => fileInput.current?.click()}
            className="inline-flex items-center gap-1.5 text-sm font-medium text-brand-600 hover:text-brand-700"
          >
            <Upload className="h-3.5 w-3.5" />
            Upload a file
          </button>
        }
      >
        <input
          ref={fileInput}
          type="file"
          accept=".txt,.md,.markdown,text/*"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void readFile(f);
            e.target.value = "";
          }}
        />

        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const f = e.dataTransfer.files?.[0];
            if (f) void readFile(f);
          }}
        >
          <textarea
            value={jd}
            onChange={(e) => setJd(e.target.value)}
            onBlur={() => jd !== iv.jd_text && onPatch({ jd_text: jd })}
            rows={12}
            placeholder="Paste the job description here, or drop a .txt file onto this box…"
            className="field-textarea font-mono text-xs leading-relaxed placeholder:font-sans"
          />
        </div>

        <div className="mt-1 flex flex-wrap items-center justify-between gap-2 text-xs text-gray-400">
          <span>{jd.trim().length.toLocaleString()} characters</span>
          {jd.trim().length > 0 && jd.trim().length <= 120 && (
            <span className="text-warning-700">
              That's very short for a job description — the analysis will be thin.
            </span>
          )}
        </div>

        {fileNote && (
          <div className="mt-3">
            <Callout tone="info">
              <span className="inline-flex items-start gap-2">
                <FileText className="mt-0.5 h-4 w-4 shrink-0" />
                {fileNote}
              </span>
            </Callout>
          </div>
        )}
        {error && (
          <div className="mt-3">
            <Callout tone="error" title="Couldn't analyse it">
              {error}
            </Callout>
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button
            disabled={!canExtract}
            onClick={() =>
              onExtract({
                jd_text: jd,
                role_title: iv.role_title,
                company_name: iv.company.name,
                company_about: iv.company.about,
                role_context: iv.company.role_context,
                experience_from: iv.experience_from,
                experience_to: iv.experience_to,
                skills_evaluated: iv.skills_evaluated,
              })
            }
          >
            <Sparkles className="h-4 w-4" />
            {extracting
              ? "Analysing…"
              : iv.extracted
                ? "Re-analyse the description"
                : "Analyse the description"}
          </Button>
          {iv.extracted && (
            <span className="text-sm text-gray-400">
              Re-analysing replaces the outcomes, tasks, and skills below.
            </span>
          )}
          {extracting && (
            <span className="text-sm text-gray-500">
              Reading the role — this takes about fifteen seconds.
            </span>
          )}
        </div>
      </Section>
    </>
  );
}
