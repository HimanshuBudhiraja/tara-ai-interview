import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Check, Sparkles } from "lucide-react";
import {
  adminApi,
  FormError,
  GenerationError,
  type FieldErrors,
  type FunnelStage,
  type GenerateInput,
  type GenerationProgress,
} from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Button, Callout, Spinner } from "@tara/ui/primitives";
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
  const [progressToken, setProgressToken] = useState("");
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
    // A throwaway id so the screen can ask the server what it is doing. Minted
    // per attempt, not per screen: a retry after a failure is a new generation
    // and must not read the previous one's board.
    const token =
      globalThis.crypto?.randomUUID?.() ??
      `pg_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
    setProgressToken(token);
    setGenerating(true);
    setErrors({});
    setFailure(null);
    try {
      const draft = await adminApi.generate({ ...form, progress_token: token });
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
      setProgressToken("");
    }
  }

  if (generating) return <Generating title={form.title} token={progressToken} />;

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
/** The three stages the server actually moves through, in order. */
const STAGES = [
  { id: "preparing", label: "Reading the job description" },
  { id: "designing", label: "Working out the skills and tasks" },
  { id: "writing_questions", label: "Writing the questions" },
] as const;

/**
 * What the generation is doing, while it does it.
 *
 * This screen used to be a pulsing icon and a paragraph, which looks exactly
 * the same at five seconds and at a hundred and fifty — so the one question a
 * recruiter actually has ("is this working, or has it hung?") was the one
 * thing it could not answer. Two and a half minutes of no feedback is how you
 * train people to reload the page halfway through.
 *
 * Every number here is reported by the server and is something that has
 * already happened: a stage entered, a question finished. There is no
 * percentage and no bar that fills on a timer — a timed bar keeps filling
 * while a hung request goes nowhere, which is precisely when it would be
 * lying. When there is nothing to report the screen says so and keeps the
 * elapsed clock running, which is honest and still reassuring.
 */
function Generating({ title, token }: { title: string; token: string }) {
  const [progress, setProgress] = useState<GenerationProgress | null>(null);
  // Client-side, so the clock keeps moving between polls rather than ticking
  // once every two seconds.
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const started = Date.now();
    const tick = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(tick);
  }, []);

  useEffect(() => {
    if (!token) return;
    let live = true;
    const poll = async () => {
      try {
        const next = await adminApi.generationProgress(token);
        // "unknown" means this worker has no record of the token — no news,
        // not failure. Keep whatever was last known rather than flickering
        // the screen back to its empty state.
        if (live && next.stage !== "unknown") setProgress(next);
      } catch {
        // A failed poll is a failed poll. The generation is driven by the
        // POST and is unaffected; showing an error here would be reporting a
        // problem the recruiter does not have.
      }
    };
    void poll();
    const id = setInterval(poll, 2000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [token]);

  const current = progress?.stage ?? "preparing";
  const index = STAGES.findIndex((s) => s.id === current);
  // `complete` and `failed` are past every stage.
  const reached = index === -1 ? STAGES.length : index;

  return (
    <div className="card mx-auto max-w-lg px-6 py-12">
      <div className="grid place-items-center text-center">
        <div className="brand-gradient grid h-10 w-10 animate-pulse place-items-center rounded-lg text-white">
          <Sparkles className="h-5 w-5" />
        </div>
        <p className="mt-4 text-md font-semibold text-gray-900">
          Building your interview for {title || "this role"}
        </p>
        <p className="mt-1 max-w-[46ch] text-sm leading-relaxed text-gray-500">
          You get a complete interview to review — skills, the tasks behind them, and a
          question for each — rather than a list to act on.
        </p>
      </div>

      <ol className="mt-7 space-y-3">
        {STAGES.map((stage, i) => {
          const done = i < reached;
          const active = i === reached;
          return (
            <li key={stage.id} className="flex items-center gap-3">
              <span
                className={cn(
                  "grid h-5 w-5 shrink-0 place-items-center rounded-full border text-2xs font-semibold transition-colors",
                  done
                    ? "border-success-200 bg-success-50 text-success-600"
                    : active
                      ? "border-brand-300 bg-brand-50 text-brand-600"
                      : "border-gray-200 bg-gray-50 text-gray-300",
                )}
              >
                {done ? <Check className="h-3 w-3" strokeWidth={3} /> : i + 1}
              </span>
              <span
                className={cn(
                  "text-sm transition-colors",
                  done ? "text-gray-500" : active ? "font-medium text-gray-900" : "text-gray-400",
                )}
              >
                {stage.label}
              </span>
              {/* The only real number on the screen: questions actually
                  written, out of questions planned. Shown only once the
                  server has reported a total, so it never renders "0 of 0". */}
              {active && stage.id === "writing_questions" && (progress?.total ?? 0) > 0 && (
                <span className="tabular ml-auto text-sm font-medium text-gray-500">
                  {progress!.done} of {progress!.total}
                </span>
              )}
              {active && <Spinner className="ml-auto h-3.5 w-3.5 shrink-0 text-gray-400" />}
            </li>
          );
        })}
      </ol>

      <div className="mt-6 flex items-center justify-between border-t border-gray-100 pt-4">
        <p className="text-xs text-gray-400">
          {elapsed < 150
            ? "Usually around two minutes. Writing the questions is the slow part."
            : "Taking longer than usual. It is still running — nothing has been lost."}
        </p>
        <p className="tabular shrink-0 text-xs text-gray-400">
          {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
        </p>
      </div>
    </div>
  );
}
