"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Wallet,
  ScrollText,
  ShieldAlert,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useSystemStatus } from "@/lib/hooks/use-risk";

const navItems = [
  { href: "/", label: "CMD", icon: LayoutDashboard },
  { href: "/portfolio", label: "PORT", icon: Wallet },
  { href: "/decisions", label: "DEC", icon: ScrollText },
  { href: "/risk", label: "RISK", icon: ShieldAlert },
];

export function Sidebar() {
  const pathname = usePathname();
  const { data: statusData, error } = useSystemStatus();

  const holdActive = statusData?.active ?? false;

  let statusLabel: string;
  let dotColor: string;
  if (error) {
    statusLabel = "ERROR";
    dotColor = "bg-danger";
  } else if (holdActive) {
    statusLabel = "HOLD";
    dotColor = "bg-amber";
  } else {
    statusLabel = "ONLINE";
    dotColor = "bg-success";
  }

  return (
    <aside className="flex w-[60px] flex-col items-center border-r border-border-subtle bg-bg-surface py-4 gap-1">
      {/* Logo */}
      <div className="mb-6 flex h-10 w-10 items-center justify-center rounded-lg bg-primary-muted">
        <Zap className="h-5 w-5 text-primary" />
      </div>

      {/* Nav items */}
      <nav className="flex flex-1 flex-col items-center gap-1">
        {navItems.map(({ href, label, icon: Icon }) => {
          const isActive = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "group relative flex w-12 flex-col items-center gap-1 rounded-lg px-2 py-2.5 transition-all duration-200",
                isActive
                  ? "bg-primary-muted text-primary"
                  : "text-text-tertiary hover:bg-bg-hover hover:text-text-secondary"
              )}
            >
              {isActive && (
                <div className="absolute left-0 top-1/2 h-6 w-0.5 -translate-y-1/2 rounded-r bg-primary" />
              )}
              <Icon className="h-[18px] w-[18px]" strokeWidth={1.5} />
              <span className="font-mono text-[9px] font-medium tracking-wide">
                {label}
              </span>
            </Link>
          );
        })}
      </nav>

      {/* System status */}
      <div className="mt-auto flex flex-col items-center gap-2 pt-4">
        <div className="flex flex-col items-center gap-1">
          <div className={cn("h-1.5 w-1.5 rounded-full", dotColor, !error && !holdActive && "animate-pulse-glow")} />
          <span className="font-mono text-[9px] font-medium tracking-wide text-text-secondary">
            {statusLabel}
          </span>
        </div>
      </div>
    </aside>
  );
}
