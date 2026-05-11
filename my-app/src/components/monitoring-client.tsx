"use client";

import { useEffect } from "react";

import { API_BASE_URL, getAuthToken } from "@/lib/api";

const HEARTBEAT_INTERVAL_MS = 30_000;

async function sendHeartbeat() {
  if (typeof window === "undefined") return;

  const token = getAuthToken();
  if (!token) return;

  try {
    await fetch(`${API_BASE_URL}/api/monitoring/heartbeat`, {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Page-Path": window.location.pathname + window.location.search,
      },
      body: JSON.stringify({}),
    });
  } catch {
    // 모니터링 실패는 사용자 기능을 막지 않도록 조용히 무시합니다.
  }
}

export function MonitoringClient() {
  useEffect(() => {
    void sendHeartbeat();

    const intervalId = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void sendHeartbeat();
      }
    }, HEARTBEAT_INTERVAL_MS);

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void sendHeartbeat();
      }
    };

    window.addEventListener("focus", sendHeartbeat);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("focus", sendHeartbeat);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  return null;
}
