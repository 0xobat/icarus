"use client";

import { useState, useEffect, useRef, useCallback } from "react";

type EventCallback = (data: unknown) => void;

interface UseEventStreamReturn {
  subscribe: (eventType: string, callback: EventCallback) => () => void;
  connected: boolean;
  error: string | null;
}

/** SSE connection manager for /api/events with reconnect backoff and REST polling fallback. */
export function useEventStream(): UseEventStreamReturn {
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listenersRef = useRef<Map<string, Set<EventCallback>>>(new Map());
  const sseFailCountRef = useRef(0);
  const eventSourceRef = useRef<EventSource | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const backoffRef = useRef(1000);

  const dispatch = useCallback((eventType: string, data: unknown) => {
    const callbacks = listenersRef.current.get(eventType);
    if (callbacks) {
      callbacks.forEach((cb) => cb(data));
    }
  }, []);

  useEffect(() => {
    function startPolling() {
      if (pollingRef.current) return;
      pollingRef.current = setInterval(() => {
        setConnected(true);
        // Polling triggers re-fetches through hooks' own intervals
      }, 10000);
    }

    function connectSSE() {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }

      const es = new EventSource("/api/events");
      eventSourceRef.current = es;

      es.onopen = () => {
        setConnected(true);
        setError(null);
        sseFailCountRef.current = 0;
        backoffRef.current = 1000;
      };

      es.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data);
          if (parsed.eventType) {
            dispatch(parsed.eventType, parsed);
          }
        } catch {
          // ignore parse errors
        }
      };

      es.onerror = () => {
        es.close();
        eventSourceRef.current = null;
        setConnected(false);
        sseFailCountRef.current += 1;

        if (sseFailCountRef.current >= 3) {
          setError("SSE failed, falling back to polling");
          startPolling();
          return;
        }

        const delay = Math.min(backoffRef.current, 30000);
        backoffRef.current = delay * 2;
        setTimeout(connectSSE, delay);
      };
    }

    connectSSE();

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [dispatch]);

  const subscribe = useCallback(
    (eventType: string, callback: EventCallback) => {
      if (!listenersRef.current.has(eventType)) {
        listenersRef.current.set(eventType, new Set());
      }
      listenersRef.current.get(eventType)!.add(callback);

      return () => {
        const callbacks = listenersRef.current.get(eventType);
        if (callbacks) {
          callbacks.delete(callback);
          if (callbacks.size === 0) {
            listenersRef.current.delete(eventType);
          }
        }
      };
    },
    []
  );

  return { subscribe, connected, error };
}
