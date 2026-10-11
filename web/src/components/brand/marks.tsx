import { cn } from "@/lib/utils";

/**
 * The brand marks (docs/design/logos). Argon: the full outer shell of eight
 * electrons (argon is inert, it never reacts on its own) around a navy core.
 * Argus, the AI: an eye with argon's violet glow as the iris and a navy spark.
 * Lines take the text colour, so both work in light and dark mode.
 */
const navy = "var(--mark-navy, var(--brand-navy, #1e3a8a))";

const SHELL = [0, 45, 90, 135, 180, 225, 270, 315].map((deg) => [32 + 22 * Math.sin((deg * Math.PI) / 180), 32 - 22 * Math.cos((deg * Math.PI) / 180)]);

export function ArgonMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" aria-hidden className={cn("size-6 shrink-0", className)}>
      <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="1.5" opacity=".25" />
      {SHELL.map(([x, y]) => (
        <circle key={`${x}-${y}`} cx={x} cy={y} r="3.6" fill="currentColor" />
      ))}
      <circle cx="32" cy="32" r="9" fill={navy} />
    </svg>
  );
}

export function ArgusMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" aria-hidden className={cn("size-4 shrink-0", className)}>
      <path d="M5 33C15 17 49 17 59 33C49 49 15 49 5 33Z" fill="none" stroke="currentColor" strokeWidth="4.5" strokeLinejoin="round" />
      <circle cx="32" cy="33" r="10.5" fill="var(--brand-violet, #7c5cff)" />
      <circle cx="32" cy="33" r="4.2" fill="#09090b" />
      <circle cx="35.2" cy="29.6" r="1.6" fill="#fff" />
      <path d="M53 5.5C53.8 9.6 55.4 11.2 59.5 12C55.4 12.8 53.8 14.4 53 18.5C52.2 14.4 50.6 12.8 46.5 12C50.6 11.2 52.2 9.6 53 5.5Z" fill={navy} />
    </svg>
  );
}
