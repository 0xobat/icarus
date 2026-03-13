"use client";

import { useState, useEffect, useCallback } from "react";
import { apiFetch } from "./use-api";
import type { Position, ChartPoint, ReserveData } from "@/lib/types";

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

export function usePositions(): HookResult<Position[]> {
  return useFetchHook<Position[]>("/api/portfolio/positions");
}

export function useSnapshots(range = "24h"): HookResult<ChartPoint[]> {
  return useFetchHook<ChartPoint[]>(`/api/portfolio/snapshots?range=${range}`);
}

export function useReserve(): HookResult<ReserveData> {
  return useFetchHook<ReserveData>("/api/portfolio/reserve");
}
