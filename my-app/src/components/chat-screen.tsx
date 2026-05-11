"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { API_BASE_URL, apiFetch, getAuthToken } from "@/lib/api";
import { generateClientId } from "@/lib/id";
import { AppSidebar } from "@/components/app-sidebar";
import { AssistantRichMessage } from "@/components/assistant-rich-message";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};

type ChatSession = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  process: string;
  reference_doc_count: number;
  messages: ChatMessage[];
};

type PendingState = {
  userMessage: ChatMessage;
  assistantMessage: ChatMessage;
} | null;

const CHAT_SESSION_EVENT = "chat-sessions-changed";
const REFERENCE_DOC_OPTIONS = [1, 3, 5, 10, 20, 30] as const;
const DEFAULT_REFERENCE_DOC_COUNT = 5;

function buildPendingState(content: string): PendingState {
  const now = new Date().toISOString();
  return {
    userMessage: {
      id: `pending-user-${generateClientId()}`,
      role: "user",
      content,
      created_at: now,
    },
    assistantMessage: {
      id: `pending-assistant-${generateClientId()}`,
      role: "assistant",
      content: "",
      created_at: now,
    },
  };
}

export function ChatScreen() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const chatId = searchParams.get("chat");
  const scrollAreaRef = useRef<HTMLDivElement | null>(null);

  const [session, setSession] = useState<ChatSession | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingState, setPendingState] = useState<PendingState>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [referenceDocCount, setReferenceDocCount] = useState<number>(DEFAULT_REFERENCE_DOC_COUNT);
  const [updatingDocCount, setUpdatingDocCount] = useState(false);

  const isEmpty = useMemo(() => !session || session.messages.length === 0, [session]);
  const visibleMessages = useMemo(() => {
    const base = session?.messages ?? [];
    if (!pendingState) return base;
    return [...base, pendingState.userMessage, pendingState.assistantMessage];
  }, [pendingState, session?.messages]);

  async function createChatAndMove() {
    const created = await apiFetch<ChatSession>("/api/chat/sessions", {
      method: "POST",
      body: JSON.stringify({ process: "MP", reference_doc_count: DEFAULT_REFERENCE_DOC_COUNT }),
    });
    window.dispatchEvent(new Event(CHAT_SESSION_EVENT));
    router.replace(`/?chat=${created.id}`);
  }

  async function loadSession(targetId: string) {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<ChatSession>(`/api/chat/sessions/${targetId}`);
      setSession(data);
      setReferenceDocCount(data.reference_doc_count ?? DEFAULT_REFERENCE_DOC_COUNT);
    } catch (loadError) {
      setSession(null);
      setError(loadError instanceof Error ? loadError.message : "채팅을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!chatId) {
      setSession(null);
      setPendingState(null);
      setLoading(false);
      setError(null);
      setReferenceDocCount(DEFAULT_REFERENCE_DOC_COUNT);
      return;
    }
    void loadSession(chatId);
  }, [chatId]);

  useEffect(() => {
    const element = scrollAreaRef.current;
    if (!element) return;
    element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
  }, [loading, pendingState, session?.messages.length]);

  async function handleCopy(content: string, messageId: string) {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedMessageId(messageId);
      window.setTimeout(() => {
        setCopiedMessageId((current) => (current === messageId ? null : current));
      }, 1600);
    } catch {
      setCopiedMessageId(null);
    }
  }

  async function handleReferenceDocCountChange(nextValue: number) {
    if ([20, 30].includes(nextValue)) {
      const confirmed = window.confirm(
        `참조 문서 수를 ${nextValue}개로 선택하면 답변 속도가 느려질 수 있습니다. 계속 진행하시겠습니까?`,
      );
      if (!confirmed) return;
    }

    setReferenceDocCount(nextValue);
    if (!chatId) return;

    setUpdatingDocCount(true);
    try {
      const updated = await apiFetch<ChatSession>(`/api/chat/sessions/${chatId}/reference-doc-count`, {
        method: "PATCH",
        body: JSON.stringify({ reference_doc_count: nextValue }),
      });
      setSession(updated);
      setReferenceDocCount(updated.reference_doc_count ?? nextValue);
      window.dispatchEvent(new Event(CHAT_SESSION_EVENT));
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "참조 문서 수 변경에 실패했습니다.");
      setReferenceDocCount(session?.reference_doc_count ?? DEFAULT_REFERENCE_DOC_COUNT);
    } finally {
      setUpdatingDocCount(false);
    }
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!chatId || !input.trim() || sending) return;

    const content = input.trim();
    const pending = buildPendingState(content);
    setInput("");
    setSending(true);
    setError(null);
    setPendingState(pending);

    try {
      const token = getAuthToken();
      const headers = new Headers({
        "Content-Type": "application/json",
      });
      if (token) {
        headers.set("Authorization", `Bearer ${token}`);
      }

      const response = await fetch(`${API_BASE_URL}/api/chat/sessions/${chatId}/messages/stream`, {
        method: "POST",
        headers,
        body: JSON.stringify({ content, reference_doc_count: referenceDocCount }),
        credentials: "include",
        cache: "no-store",
      });

      if (response.status === 401) {
        window.location.href = "/login";
        throw new Error("인증이 만료되었습니다.");
      }

      if (!response.ok || !response.body) {
        const text = await response.text();
        throw new Error(text || "질문 전송에 실패했습니다.");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finalSession: ChatSession | null = null;
      let streamError: string | null = null;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const rawLine of lines) {
          const line = rawLine.trim();
          if (!line) continue;

          let payload: {
            type: "user_ack" | "delta" | "final" | "error";
            content?: string;
            message?: string;
            session?: ChatSession;
            answer?: string;
          };

          try {
            payload = JSON.parse(line);
          } catch {
            continue;
          }

          if (payload.type === "delta") {
            const delta = payload.content ?? "";
            if (!delta) continue;
            setPendingState((current) => {
              if (!current) return current;
              return {
                ...current,
                assistantMessage: {
                  ...current.assistantMessage,
                  content: `${current.assistantMessage.content}${delta}`,
                },
              };
            });
          } else if (payload.type === "error") {
            streamError = payload.message ?? "답변 스트리밍 중 오류가 발생했습니다.";
          } else if (payload.type === "final") {
            finalSession = payload.session ?? null;
          }
        }
      }

      if (buffer.trim()) {
        try {
          const payload = JSON.parse(buffer.trim()) as {
            type: "user_ack" | "delta" | "final" | "error";
            content?: string;
            message?: string;
            session?: ChatSession;
          };
          if (payload.type === "final") {
            finalSession = payload.session ?? null;
          } else if (payload.type === "error") {
            streamError = payload.message ?? "답변 스트리밍 중 오류가 발생했습니다.";
          } else if (payload.type === "delta") {
            const delta = payload.content ?? "";
            if (delta) {
              setPendingState((current) => {
                if (!current) return current;
                return {
                  ...current,
                  assistantMessage: {
                    ...current.assistantMessage,
                    content: `${current.assistantMessage.content}${delta}`,
                  },
                };
              });
            }
          }
        } catch {
          // ignore trailing partial line
        }
      }

      if (streamError) {
        throw new Error(streamError);
      }

      if (finalSession) {
        setSession(finalSession);
        setPendingState(null);
        window.dispatchEvent(new Event(CHAT_SESSION_EVENT));
      } else {
        throw new Error("스트리밍 응답이 정상적으로 완료되지 않았습니다.");
      }
    } catch (sendError) {
      setPendingState(null);
      setError(sendError instanceof Error ? sendError.message : "질문 전송에 실패했습니다.");
      setInput(content);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="app-shell">
      <AppSidebar onCreateChat={createChatAndMove} />

      <main className="main-panel chat-panel">
        <div className="chat-scroll-area" ref={scrollAreaRef}>
          {loading ? (
            <div className="empty-state">
              <p className="muted-text">채팅을 준비하는 중입니다...</p>
            </div>
          ) : null}

          {!loading && error ? (
            <div className="empty-state">
              <p className="error-text">{error}</p>
            </div>
          ) : null}

          {!loading && !error ? (
            <div className="chat-scroll-inner">
              {isEmpty && !pendingState ? (
                <section className="welcome-card enhanced">
                  <div className="welcome-hero-mark">AI</div>
                  <p className="welcome-label">CHAT TEST</p>
                  <h1>무엇을 도와드릴까요?</h1>
                  <p className="muted-text">
                    설비 에러 이력, 점검 방법, 일반 질문까지 한 화면에서 빠르게 확인할 수 있도록
                    답변을 더 읽기 쉽게 정리해드립니다.
                  </p>
                  <div className="prompt-suggestion-grid">
                    <div className="prompt-suggestion-card">
                      <span className="suggestion-title">설비 이력 확인</span>
                      <span className="suggestion-body">예: 스태커 1호기 정렬 오류 582 이력 알려줘</span>
                    </div>
                    <div className="prompt-suggestion-card">
                      <span className="suggestion-title">원인/조치 요약</span>
                      <span className="suggestion-body">예: 반복 발생 원인과 우선 조치 순서로 정리해줘</span>
                    </div>
                    <div className="prompt-suggestion-card">
                      <span className="suggestion-title">현장 공유용 정리</span>
                      <span className="suggestion-body">예: 작업자 보고용으로 핵심만 bullet로 정리해줘</span>
                    </div>
                  </div>
                </section>
              ) : null}

              {visibleMessages.map((message) => {
                const isPendingAssistant =
                  pendingState?.assistantMessage.id === message.id && message.role === "assistant";
                const isAssistant = message.role === "assistant";
                const isCopied = copiedMessageId === message.id;

                return (
                  <article
                    key={message.id}
                    className={message.role === "user" ? "message-row user" : "message-row assistant"}
                  >
                    <div className={message.role === "user" ? "message-avatar user" : "message-avatar assistant"}>
                      {message.role === "user" ? "나" : "AI"}
                    </div>

                    <div className="message-stack">
                      <div className="message-topline">
                        <div className="message-role">
                          {message.role === "user" ? "나의 질문" : "Assistant"}
                          {isPendingAssistant ? <span className="message-status">답변 생성 중...</span> : null}
                        </div>

                        {isAssistant && !isPendingAssistant ? (
                          <div className="assistant-actions">
                            <span className="assistant-badge">정리된 응답</span>
                            <button
                              className="assistant-copy-button"
                              type="button"
                              onClick={() => void handleCopy(message.content, message.id)}
                            >
                              {isCopied ? "복사됨" : "복사"}
                            </button>
                          </div>
                        ) : null}
                      </div>

                      <div
                        className={
                          isPendingAssistant
                            ? "message-bubble assistant-card thinking"
                            : isAssistant
                              ? "message-bubble assistant-card"
                              : "message-bubble user-card"
                        }
                      >
                        {isPendingAssistant ? (
                          message.content ? (
                            <div className="streaming-answer-shell">
                              <div className="streaming-answer-badge">실시간 생성 중...</div>
                              <AssistantRichMessage content={message.content} activeChatId={chatId ?? undefined} />
                            </div>
                          ) : (
                            <div className="assistant-thinking-shell">
                              <div className="assistant-thinking-title">문서를 확인하고 응답을 정리하고 있습니다</div>
                              <div className="typing-indicator" aria-label="답변 생성 중">
                                <span />
                                <span />
                                <span />
                              </div>
                            </div>
                          )
                        ) : isAssistant ? (
                          <AssistantRichMessage content={message.content} activeChatId={chatId ?? undefined} />
                        ) : (
                          <p>{message.content}</p>
                        )}
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : null}
        </div>

        <form className="composer" onSubmit={handleSubmit}>
          <div className="composer-inner">
            <textarea
              className="composer-input"
              disabled={!chatId || sending}
              rows={4}
              placeholder={
                chatId
                  ? "반도체 설비 에러, 점검 이력, 일반 질문 등을 입력하세요. 관련 점검 이력은 자동 검색 후 바로 답변합니다."
                  : "왼쪽의 새 채팅 버튼을 눌러 대화를 시작하세요."
              }
              value={input}
              onChange={(event) => setInput(event.target.value)}
            />
            <div className="composer-bottom">
              <div className="composer-controls">
                <div className="composer-select-group">
                  <label className="composer-select-label" htmlFor="reference-doc-count-select">
                    참조 문서 수
                  </label>
                  <select
                    id="reference-doc-count-select"
                    className="composer-select"
                    value={referenceDocCount}
                    disabled={!chatId || sending || updatingDocCount}
                    onChange={(event) => void handleReferenceDocCountChange(Number(event.target.value))}
                  >
                    {REFERENCE_DOC_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </div>

                <span className="muted-text small composer-status-text">
                  {sending
                    ? "질문을 전송했고 답변을 실시간으로 생성하고 있습니다..."
                    : updatingDocCount
                      ? "참조 문서 수를 저장하는 중입니다..."
                      : "공정 기본값: MP"}
                </span>
              </div>

              <button className="primary-button" disabled={sending || !chatId} type="submit">
                {sending ? "답변 생성 중..." : "질문 보내기"}
              </button>
            </div>
          </div>
        </form>
      </main>
    </div>
  );
}
