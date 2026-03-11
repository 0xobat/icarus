interface ConnectionBannerProps {
  lastUpdate: string | null;
}

/** Full-width red banner shown when the data connection is lost. */
export function ConnectionBanner({ lastUpdate }: ConnectionBannerProps) {
  if (lastUpdate === null) return null;

  return (
    <div className="w-full border-b border-danger/20 bg-danger-muted px-4 py-2 text-center font-mono text-xs text-danger">
      CONNECTION LOST &mdash; Last update {lastUpdate}
    </div>
  );
}
