import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Sparkles } from "lucide-react";
import {
  adminApi,
  FormError,
  GenerationError,
  type FieldErrors,
  type FunnelStage,
  type GenerateInput,
} from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Button, Callout } from "@tara/ui/primitives";
import { Block, Steps } from "../components/Wizard";
import { cn } from "../lib/cn";

/**
 * Step 1 of three — the only place the recruiter is asked to type anything.
 *
 * The steps are shown because a recruiter pasting a job description into a
 * single unlabelled form has no idea whether they are one screen from done or
 * five. What follows is review and invitation, not more data entry: this page
 * asks for everything the analysis needs and nothing it can work out itself.
 *
 * Grouped into four blocks rather than one long card. Job role, scope,
 * description, context — each answering a different question, each short
 * enough to take in at a glance.
 */

const EMPTY: GenerateInput = {
  title: "",
  experience_from: "",
  experience_to: "",
  language: "en",
  funnel_stage: "",
  job_description: "",
  additional_information: "",
};

const YEARS = Array.from({ length: 21 }, (_, n) => n);

export function CreateInterview() {
  const [form, setForm] = useState<GenerateInput>(EMPTY);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [generating, setGenerating] = useState(false);
  const [failure, setFailure] = useState<{ message: string; id?: string } | null>(null);
  const [languages, setLanguages] = useState([{ code: "en", label: "English" }]);
  const [stages, setStages] = useState<FunnelStage[]>([]);
  const firstError = useRef<HTMLElement | null>(null);

  // Both lists are served rather than hard-coded, so a dropdown can never
  // offer a language the interview cannot be conducted in, or a stage the
  // backend would reject.
  useEffect(() => {
    adminApi.languages().then((d) => setLanguages(d.languages)).catch(() => undefined);
    adminApi.funnelStages().then((d) => setStages(d.stages)).catch(() => undefined);
  }, []);

  const set = <K extends keyof GenerateInput>(key: K, value: GenerateInput[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    // Clear the field's error as soon as they start fixing it. Leaving it
    // there while they type reads as the fix not counting.
    setErrors((e) => (key in e ? { ...e, [key]: "" } : e));
  };

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setGenerating(true);
    setErrors({});
    setFailure(null);
    try {
      const draft = await adminApi.generate(form);
      navigate({ name: "recommended", id: draft.id });
    } catch (err) {
      if (err instanceof FormError) {
        setErrors(err.errors);
        requestAnimationFrame(() => firstError.current?.scrollIntoView({
          behavior: "smooth", block: "center",
        }));
      } else if (err instanceof GenerationError) {
        setFailure({ message: err.message, id: err.interviewId });
      } else {
        setFailure({ message: (err as Error).message });
      }
    } finally {
      setGenerating(false);
    }
  }

  if (generating) return <Generating title={form.title} />;

  const chosenStage = stages.find((s) => s.id === form.funnel_stage);

  return (
    <form onSubmit={submit} className="max-w-3xl space-y-6" noValidate>
      <Button variant="link" type="button" onClick={() => navigate({ name: "dashboard" })}>
        <ArrowLeft className="h-4 w-4" />
        All interviews
      </Button>

      <Steps current={1} />

      <header>
        <h2 className="text-xl font-semibold tracking-tight text-gray-900">
          Tell Tara about the role
        </h2>
        <p className="mt-1 max-w-[62ch] text-sm leading-relaxed text-gray-500">
          Everything after this is review. Tara reads what you paste here and proposes the
          skills, the tasks behind them and the questions — you correct it before anything
          reaches a candidate.
        </p>
      </header>

      {failure && (
        <Callout tone="error" title="Tara couldn't build the recommendation">
          <p>{failure.message}</p>
          {failure.id && (
            <p className="mt-2">
              <Button
                variant="secondary"
                size="sm"
                type="button"
                onClick={() => navigate({ name: "recommended", id: failure.id! })}
              >
                Open it and retry
              </Button>
            </p>
          )}
        </Callout>
      )}

      {/* --- 1. the role ------------------------------------------------- */}
      <Block title="Job role">
        <Field
          label="AI interview title"
          hint="The job role. This is what appears on the candidate's invitation and on every report."
          error={errors.title}
          required
        >
          <input
            value={form.title}
            onChange={(e) => set("title", e.target.value)}
            placeholder="e.g. Senior Java Developer"
            className={inputClass(errors.title)}
          />
        </Field>
      </Block>

      {/* --- 2. scope ------------------------------------------------------ */}
      <Block
        title="Interview scope"
        hint="These three decide how deep Tara goes. The same answer is judged differently at two years and at ten."
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Years of experience" error={errors.experience_from || errors.experience_to} required>
            <div className="flex items-center gap-2">
              <select
                value={form.experience_from}
                onChange={(e) => set("experience_from", e.target.value)}
                aria-label="Experience from"
                className={cn(inputClass(errors.experience_from), "w-full")}
              >
                <option value="">From</option>
                {YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
              <span className="shrink-0 text-sm text-gray-400">to</span>
              <select
                value={form.experience_to}
                onChange={(e) => set("experience_to", e.target.value)}
                aria-label="Experience to"
                className={cn(inputClass(errors.experience_to), "w-full")}
              >
                <option value="">To</option>
                {YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
            </div>
          </Field>

          <Field label="Language" error={errors.language} required>
            <select
              value={form.language}
              onChange={(e) => set("language", e.target.value)}
              className={inputClass(errors.language)}
            >
              {languages.map((l) => (
                <option key={l.code} value={l.code}>{l.label}</option>
              ))}
            </select>
          </Field>
        </div>

        <Field
          label="Recruitment funnel stage"
          hint="What stage of the process this interview is for. Tara uses it to decide how long the interview runs and how hard to push."
          error={errors.funnel_stage}
          required
        >
          <select
            value={form.funnel_stage}
            onChange={(e) => set("funnel_stage", e.target.value)}
            className={inputClass(errors.funnel_stage)}
          >
            <option value="">Select stage</option>
            {stages.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
          {/* What the choice actually does, shown the moment it is made rather
              than discovered two screens later on the review page. */}
          {chosenStage && (
            <p className="mt-1.5 text-xs text-gray-500">
              A <strong className="font-medium text-gray-700">{chosenStage.interview_type}</strong>{" "}
              interview at <strong className="font-medium text-gray-700">{chosenStage.difficulty}</strong>{" "}
              difficulty. The stage sets how hard Tara pushes; you can still change
              the length on the next screen.
            </p>
          )}
        </Field>
      </Block>

      {/* --- 3. the JD ---------------------------------------------------- */}
      <Block
        title="Job description"
        hint="Paste it in full. This is the only thing the analysis reads — everything Tara proposes is derived from it."
      >
        <Field label="Job description" error={errors.job_description} required hideLabel>
          <textarea
            value={form.job_description}
            onChange={(e) => set("job_description", e.target.value)}
            rows={14}
            placeholder="Paste the full job description — responsibilities, requirements, the lot."
            className={cn(inputClass(errors.job_description), "h-auto min-h-[260px] py-2.5 leading-relaxed")}
          />
        </Field>
        <p className="text-right text-xs tabular-nums text-gray-400">
          {form.job_description.length.toLocaleString()} characters
        </p>
      </Block>

      {/* --- 4. context ---------------------------------------------------- */}
      <Block
        title="Additional context"
        hint="Optional. What the description leaves out but the interview should lean on."
      >
        <Field label="Additional information" error={errors.additional_information} hideLabel>
          <textarea
            value={form.additional_information}
            onChange={(e) => set("additional_information", e.target.value)}
            rows={4}
            placeholder="e.g. Kafka and idempotency matter more than breadth. The team carries on-call."
            className={cn(inputClass(errors.additional_information), "h-auto min-h-[100px] py-2.5 leading-relaxed")}
          />
        </Field>
        <p className="text-xs leading-relaxed text-gray-500">
          This shapes what Tara <em>assesses</em>. It is not a script for answering candidate
          questions about pay, benefits or process — Tara tells candidates the hiring team will
          be in touch about those.
        </p>
      </Block>

      <div className="flex flex-wrap items-center gap-3 border-t border-gray-200 pt-5">
        <Button type="submit">
          <Sparkles className="h-4 w-4" />
          Configure interview
        </Button>
        <Button variant="ghost" type="button" onClick={() => setForm(EMPTY)}>
          Reset
        </Button>
        <p className="text-sm text-gray-500">Nothing is published. You review it next.</p>
      </div>
    </form>
  );
}



function inputClass(error?: string) {
  return cn(
    "h-9 w-full rounded-md border bg-surface px-3 text-base text-gray-900 shadow-xs",
    "placeholder:text-gray-400 focus:outline-none focus:ring-2",
    error
      ? "border-error-300 focus:border-error-400 focus:ring-error-100"
      : "border-gray-300 focus:border-brand-400 focus:ring-brand-100",
  );
}

function Field({
  label,
  hint,
  error,
  required,
  hideLabel,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  /** The block's title already says it — repeating it is noise. */
  hideLabel?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className={cn("text-sm font-medium text-gray-700", hideLabel && "sr-only")}>
        {label}
        {required && !hideLabel && <span className="ml-0.5 text-error-600">*</span>}
      </span>
      {hint && !hideLabel && (
        <span className="mt-0.5 block max-w-[68ch] text-xs leading-relaxed text-gray-500">
          {hint}
        </span>
      )}
      <div className={cn(!hideLabel && "mt-1.5")}>{children}</div>
      {error && <span className="mt-1 block text-xs font-medium text-error-600">{error}</span>}
    </label>
  );
}

/**
 * The waiting state.
 *
 * Deliberately no percentage. We do not know how far through the model is, and
 * a bar that invents "Skills 83%" is a bar that stalls at 83% and makes a
 * working system look broken.
 */
function Generating({ title }: { title: string }) {
  return (
    <div className="card grid place-items-center px-6 py-20 text-center">
      <div className="brand-gradient grid h-10 w-10 animate-pulse place-items-center rounded-lg text-white">
        <Sparkles className="h-5 w-5" />
      </div>
      <p className="mt-4 text-md font-semibold text-gray-900">
        Building your recommended interview…
      </p>
      <p className="mt-1 max-w-[54ch] text-sm leading-relaxed text-gray-500">
        Tara is reading the job description for {title || "this role"}, working out the
        skills it depends on and the tasks behind them, and then writing a question for
        each. You get a complete interview to review rather than a list to act on.
      </p>
      <p className="mt-3 text-xs text-gray-400">
        Usually under two minutes. Writing the questions is the slow part.
      </p>
    </div>
  );
}
