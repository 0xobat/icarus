import { cn } from "@/lib/utils";

interface SkeletonCardProps {
  className?: string;
}

/** Pulsing card skeleton matching the Cyber Rust design system. */
export function SkeletonCard({ className }: SkeletonCardProps) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-lg border border-border-default bg-bg-surface p-4",
        className
      )}
    >
      <div className="mb-3 h-3 w-24 rounded bg-bg-elevated" />
      <div className="mb-2 h-6 w-32 rounded bg-bg-elevated" />
      <div className="h-3 w-full rounded bg-bg-elevated" />
    </div>
  );
}

interface SkeletonTableProps {
  rows?: number;
  className?: string;
}

/** Pulsing table skeleton with configurable row count. */
export function SkeletonTable({ rows = 5, className }: SkeletonTableProps) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-lg border border-border-default bg-bg-surface p-4",
        className
      )}
    >
      {/* Header row */}
      <div className="mb-3 flex gap-4">
        <div className="h-3 w-20 rounded bg-bg-elevated" />
        <div className="h-3 w-28 rounded bg-bg-elevated" />
        <div className="h-3 w-16 rounded bg-bg-elevated" />
        <div className="h-3 w-20 rounded bg-bg-elevated" />
      </div>
      <div className="mb-3 border-t border-border-subtle" />
      {/* Data rows */}
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="mb-2 flex gap-4">
          <div className="h-3 w-20 rounded bg-bg-elevated" />
          <div className="h-3 w-28 rounded bg-bg-elevated" />
          <div className="h-3 w-16 rounded bg-bg-elevated" />
          <div className="h-3 w-20 rounded bg-bg-elevated" />
        </div>
      ))}
    </div>
  );
}
