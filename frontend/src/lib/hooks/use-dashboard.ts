"use client";

import { useState, useEffect, useCallback } from "react";
import { apiFetch } from "./use-api";
import type {
  MetricsData,
  StrategiesPanelData,
  CircuitBreaker,
  ClaudeDecision,
  Execution,
} from "@/lib/types";

interface HookResult<T> {
  data: T | null;
  isLoading: boolean;
  error: string | null;
  stale: boolean;
}

function useFetchHook<T>(url: string, pollMs = 10000): HookResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const res = await apiFetch(url);
      const json = await res.json();
      if (json.stale) {
        setStale(true);
      } else {
        setData(json.data ?? json);
        setStale(false);
      }
      setError(null);
    } catch (e) {
      if (e instanceof Error && e.message === "Unauthorized") return;
      setError(`Failed to fetch ${url}`);
    } finally {
      setIsLoading(false);
    }
  }, [url]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, pollMs);
    return () => clearInterval(interval);
  }, [fetchData, pollMs]);

  return { data, isLoading, error, stale };
}

export function useDashboardMetrics(): HookResult<MetricsData> {
  return useFetchHook<MetricsData>("/api/dashboard/metrics");
}

export function useDashboardStrategies(): HookResult<StrategiesPanelData> {
  return useFetchHook<StrategiesPanelData>("/api/dashboard/strategies");
}

export function useDashboardBreakers(): HookResult<CircuitBreaker[]> {
  return useFetchHook<CircuitBreaker[]>("/api/dashboard/breakers");
}

export function useDashboardDecisions(): HookResult<ClaudeDecision[]> {
  return useFetchHook<ClaudeDecision[]>("/api/dashboard/decisions/recent");
}

export function useDashboardExecutions(): HookResult<Execution[]> {
  return useFetchHook<Execution[]>("/api/dashboard/executions/recent");
}
