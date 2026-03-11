import { type ReactNode } from "react";

interface StaleIndicatorProps {
  isStale: boolean;
}

/** Amber "STALE" badge, only renders when data is stale. */
export function StaleIndicator({ isStale }: StaleIndicatorProps) {
  if (!isStale) return null;

  return (
    <span className="rounded bg-warning-muted px-1.5 py-0.5 font-mono text-[8px] font-medium text-warning tracking-wider">
      STALE
    </span>
  );
}

interface StaleWrapperProps {
  isStale: boolean;
  children: ReactNode;
}

/** Wraps children and reduces opacity when data is stale. */
export function StaleWrapper({ isStale, children }: StaleWrapperProps) {
  return (
    <div className={isStale ? "opacity-60 transition-opacity" : "transition-opacity"}>
      {children}
    </div>
  );
}
