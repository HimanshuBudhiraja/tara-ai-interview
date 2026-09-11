/**
 * Shared frontend surface.
 *
 * Deliberately tiny: a Tailwind preset, the base stylesheet, and `cn`. Anything
 * bigger belongs to an app until a second app actually needs it — a shared
 * component library assembled in advance is a component library nobody's screens
 * quite fit.
 */
export { cn } from "./cn";
