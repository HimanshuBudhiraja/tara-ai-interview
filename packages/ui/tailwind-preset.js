/** @type {import('tailwindcss').Config} */
/**
 * The design system, in one file, shared by both apps.
 *
 * The candidate experience and the recruiter console are different products to
 * their users and the same product to the company. Two Tailwind configs would
 * drift within a month — one brand orange in the console and a slightly
 * different one on the interview screen is exactly the kind of thing nobody
 * notices until a candidate screenshots it.
 *
 * iMocha brand orange over the Untitled UI gray ramp, a compact type scale, and
 * shadows that lift rather than decorate.
 */
export default {
  theme: {
    extend: {
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"] },
      colors: {
        canvas: "#FAFAFA",
        surface: "#FFFFFF",
        brand: {
          25: "#FFF8F5", 50: "#FEEEE7", 100: "#FDD9C9", 200: "#FBB79B", 300: "#F8926B",
          400: "#F47245", 500: "#F1592A", 600: "#D6451B", 700: "#B23714", 800: "#8F2D12", 900: "#6E2410",
        },
        gray: {
          25: "#FCFCFD", 50: "#F9FAFB", 100: "#F2F4F7", 200: "#EAECF0", 300: "#D0D5DD", 400: "#98A2B3",
          500: "#667085", 600: "#475467", 700: "#344054", 800: "#1D2939", 900: "#101828", 950: "#0C111D",
        },
        success: { 25: "#F6FEF9", 50: "#ECFDF3", 100: "#DCFAE6", 200: "#ABEFC6", 500: "#12B76A", 600: "#039855", 700: "#027A48" },
        error: { 25: "#FFFBFA", 50: "#FEF3F2", 100: "#FEE4E2", 200: "#FECDCA", 500: "#F04438", 600: "#D92D20", 700: "#B42318" },
        warning: { 25: "#FFFCF5", 50: "#FFFAEB", 100: "#FEF0C7", 200: "#FEDF89", 500: "#F79009", 600: "#DC6803", 700: "#B54708" },
        blue: { 25: "#F5FAFF", 50: "#EFF8FF", 100: "#D1E9FF", 200: "#B2DDFF", 500: "#2E90FA", 600: "#1570EF", 700: "#175CD3" },
      },
      // Smaller than before across the board. Pill shapes read as consumer; a
      // 6-8px radius reads as a tool.
      borderRadius: { xs: "4px", sm: "6px", md: "8px", lg: "10px", xl: "12px", "2xl": "16px" },
      fontSize: {
        // A compact, information-dense scale. Nothing above 24px in the product.
        "2xs": ["11px", { lineHeight: "16px", letterSpacing: "0.02em" }],
        xs: ["12px", { lineHeight: "18px" }],
        sm: ["13px", { lineHeight: "20px" }],
        base: ["14px", { lineHeight: "21px" }],
        md: ["15px", { lineHeight: "23px" }],
        lg: ["16px", { lineHeight: "24px", letterSpacing: "-0.01em" }],
        xl: ["18px", { lineHeight: "26px", letterSpacing: "-0.011em" }],
        "2xl": ["20px", { lineHeight: "28px", letterSpacing: "-0.014em" }],
        "3xl": ["24px", { lineHeight: "32px", letterSpacing: "-0.018em" }],
        // Reserved for KPI values and the candidate's spoken question.
        stat: ["26px", { lineHeight: "32px", letterSpacing: "-0.02em" }],
      },
      boxShadow: {
        // Restrained: a hairline border does the separating, the shadow only
        // lifts. Nothing here casts more than 4px at rest.
        xs: "0 1px 2px rgba(16,24,40,.04)",
        sm: "0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.04)",
        md: "0 2px 4px -1px rgba(16,24,40,.06), 0 4px 8px -2px rgba(16,24,40,.06)",
        lg: "0 4px 6px -2px rgba(16,24,40,.04), 0 12px 16px -4px rgba(16,24,40,.08)",
        xl: "0 8px 8px -4px rgba(16,24,40,.03), 0 20px 24px -4px rgba(16,24,40,.08)",
        focus: "0 0 0 3px rgba(241,89,42,.14)",
        "focus-gray": "0 0 0 3px rgba(152,162,179,.16)",
      },
      transitionDuration: { DEFAULT: "160ms" },
      transitionTimingFunction: { DEFAULT: "cubic-bezier(.4,0,.2,1)" },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: { "100%": { transform: "translateX(100%)" } },
      },
      animation: {
        "fade-in": "fade-in .16s ease-out",
        "slide-up": "slide-up .18s cubic-bezier(.16,1,.3,1)",
      },
    },
  },
  plugins: [],
};