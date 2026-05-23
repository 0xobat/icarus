"use client";

import { useState, useCallback } from "react";
import { apiFetch } from "./use-api";

interface CommandResult {
  execute: (params?: Record<string, unknown>) => Promise<void>;
  loading: boolean;
  error: string | null;
  commandId: string | null;
}

function useCommand(url: string): CommandResult {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [commandId, setCommandId] = useState<string | null>(null);

  const execute = useCallback(
    async (params?: Record<string, unknown>) => {
      setLoading(true);
      setError(null);
      try {
        const res = await apiFetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(params ?? {}),
        });
        if (!res.ok) {
          const errorBody = await res.json().catch(() => ({}));
          throw new Error((errorBody as Record<string, string>).error || `Command failed: ${res.status}`);
        }
        const json = await res.json();
        setCommandId(json.command_id ?? null);
      } catch (e) {
        if (e instanceof Error && e.message === "Unauthorized") return;
        setError(e instanceof Error ? e.message : `Command failed: ${url}`);
      } finally {
        setLoading(false);
      }
    },
    [url]
  );

  return { execute, loading, error, commandId };
}

export function useStrategyToggle() {
  const activate = useCommand("/api/commands/strategy/activate");
  const deactivate = useCommand("/api/commands/strategy/deactivate");

  return {
    activate: (strategyId: string) => activate.execute({ strategy_id: strategyId }),
    deactivate: (strategyId: string) => deactivate.execute({ strategy_id: strategyId }),
    loading: activate.loading || deactivate.loading,
    error: activate.error || deactivate.error,
    commandId: activate.commandId || deactivate.commandId,
  };
}

export function useHoldMode() {
  const enter = useCommand("/api/commands/hold/enter");
  const exit = useCommand("/api/commands/hold/exit");

  return {
    enter: (reason?: string) => enter.execute(reason ? { reason } : {}),
    exit: () => exit.execute(),
    loading: enter.loading || exit.loading,
    error: enter.error || exit.error,
    commandId: enter.commandId || exit.commandId,
  };
}

export function useBreakerReset() {
  return useCommand("/api/commands/breaker/reset");
}
