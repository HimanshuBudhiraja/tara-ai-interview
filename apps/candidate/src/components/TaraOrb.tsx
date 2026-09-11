import { motion } from "framer-motion";
import type { CallPhase } from "../lib/types";
import { cn } from "../lib/cn";

/**
 * The one moving thing on the interview screen.
 *
 * A voice interface hides its own state — a candidate cannot tell "still
 * listening" from "crashed" without being shown. This answers exactly one
 * question at a glance: is it my turn?
 *
 * Restrained on purpose. An assessment a company stakes a hiring decision on
 * shouldn't look like a consumer voice assistant, so this is a calm ring rather
 * than a pulsing blob: a thin brand arc, a quiet fill, and motion measured in
 * millimetres. Every state is also legible without colour — the ring's
 * thickness, radius and motion all change — so it still reads on a poor monitor
 * or to someone with a colour-vision difference.
 */
export function TaraOrb({ phase, level = 0 }: { phase: CallPhase; level?: number }) {
  const speaking = phase === "speaking";
  const listening = phase === "listening";
  const thinking = phase === "thinking";
  const stalled = phase === "reconnecting" || phase === "failed" || phase === "connecting";

  return (
    <div className="relative grid h-[132px] w-[132px] place-items-center" aria-hidden>
      {/* Listening: the outer ring tracks the candidate's own voice, so being
          heard is visible without a separate level meter. */}
      {listening && (
        <motion.span
          className="absolute rounded-full border border-brand-300"
          animate={{
            width: 104 + level * 26,
            height: 104 + level * 26,
            opacity: 0.4 + level * 0.45,
          }}
          transition={{ type: "spring", stiffness: 280, damping: 26, mass: 0.35 }}
        />
      )}

      {/* Speaking: one slow breath. Tara's turn, nothing expected of you. */}
      {speaking && (
        <motion.span
          className="absolute rounded-full border border-brand-200"
          animate={{ width: [96, 124, 96], opacity: [0.9, 0, 0.9], height: [96, 124, 96] }}
          transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
        />
      )}

      {/* The dial itself: a hairline track with a brand arc over it. */}
      <svg viewBox="0 0 100 100" className="absolute h-[92px] w-[92px] -rotate-90">
        <circle
          cx="50"
          cy="50"
          r="47"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          className={cn(listening ? "text-brand-100" : "text-gray-200")}
        />
        <motion.circle
          cx="50"
          cy="50"
          r="47"
          fill="none"
          strokeLinecap="round"
          strokeWidth="1.5"
          className={cn(stalled ? "text-gray-300" : "text-brand-500")}
          stroke="currentColor"
          style={{ pathLength: 1 }}
          animate={
            thinking
              ? { strokeDasharray: "70 225", rotate: 360 }
              : listening
                // A minimum arc, or a quiet room renders as a broken ring and
                // reads like a glitch rather than "I'm listening".
                ? { strokeDasharray: `${110 + level * 185} 300`, rotate: 0 }
                : speaking
                  ? { strokeDasharray: "295 300", rotate: 0 }
                  : { strokeDasharray: "0 300", rotate: 0 }
          }
          transition={
            thinking
              ? { rotate: { duration: 1.4, repeat: Infinity, ease: "linear" }, strokeDasharray: { duration: 0.3 } }
              : { duration: 0.35, ease: "easeOut" }
          }
          transform-origin="50 50"
        />
      </svg>

      <motion.div
        className={cn(
          "relative grid h-[76px] w-[76px] place-items-center rounded-full border",
          stalled
            ? "border-gray-200 bg-gray-50 text-gray-400"
            : "border-brand-100 bg-brand-25 text-brand-700",
        )}
        animate={speaking ? { scale: [1, 1.02, 1] } : { scale: 1 }}
        transition={
          speaking
            ? { duration: 2.4, repeat: Infinity, ease: "easeInOut" }
            : { duration: 0.25 }
        }
      >
        <span className="text-lg font-semibold tracking-tight">Tara</span>
      </motion.div>
    </div>
  );
}
