"use client";

import { useState, useEffect, useCallback } from "react";
import { apiFetch } from "./use-api";
import type { DecisionDetail } from "@/lib/types";

interface HookResult<T> {
  data: T | null;
  isLoading: boolean;
  error: string | null;
  stale: boolean;
}

interface DecisionFilters {
  strategy?: string;
  action?: string;
  source?: string;
  limit?: number;
  cursor?: string;
}

interface DecisionsResult {
  data: DecisionDetail[];
  next_cursor: string | null;
  has_more: boolean;
}

export function useDecisions(filters?: DecisionFilters): HookResult<DecisionsResult> {
  const [data, setData] = useState<DecisionsResult | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (filters?.strategy && filters.strategy !== "All") params.set("strategy", filters.strategy);
      if (filters?.action) params.set("action", filters.action);
      if (filters?.source) params.set("source", filters.source);
      if (filters?.limit) params.set("limit", String(filters.limit));
      if (filters?.cursor) params.set("cursor", filters.cursor);
      const qs = params.toString();
      const url = `/api/decisions${qs ? `?${qs}` : ""}`;
      const res = await apiFetch(url);
      const json = await res.json();
      if (json.stale) {
        setStale(true);
      } else {
        setData(json);
        setStale(false);
      }
      setError(null);
    } catch (e) {
      if (e instanceof Error && e.message === "Unauthorized") return;
      setError("Failed to fetch decisions");
    } finally {
      setIsLoading(false);
    }
  }, [filters?.strategy, filters?.action, filters?.source, filters?.limit, filters?.cursor]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [fetchData]);

  return { data, isLoading, error, stale };
}

export function useDecisionDetail(id: string | null): HookResult<DecisionDetail> {
  const [data, setData] = useState<DecisionDetail | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);

  const fetchData = useCallback(async () => {
    if (!id) {
      setData(null);
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    try {
      const res = await apiFetch(`/api/decisions/${id}`);
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
      setError(`Failed to fetch decision ${id}`);
    } finally {
      setIsLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return { data, isLoading, error, stale };
}
