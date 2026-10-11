const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type User = { id: number; name: string; email: string };
export type DocumentItem = {
  id: number;
  filename: string;
  file_type: string;
  status: string;
  processing_error: string | null;
  created_at: string | null;
  processed_at: string | null;
  chunk_count: number;
};
export type Source = {
  document_id?: number;
  filename: string | null;
  page_number: number | null;
  chunk_id?: number;
  similarity_score: number;
  content?: string;
};
export type ConversationSource = {
  document_chunk_id: number;
  similarity_score: number;
};
export type Message = {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  created_at?: string | null;
  sources?: Source[];
};
export type Conversation = {
  id: number;
  title: string;
  created_at: string | null;
  message_count: number;
};
export type ConversationDetail = Omit<Conversation, "message_count"> & {
  messages: Array<Omit<Message, "sources"> & { sources?: ConversationSource[] }>;
};

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, token?: string | null, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");

  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      message = response.statusText || message;
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string; token_type: string }>("/auth/login", null, {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  register: (name: string, email: string, password: string) =>
    request<User>("/auth/register", null, {
      method: "POST",
      body: JSON.stringify({ name, email, password }),
    }),
  me: (token: string) => request<User>("/auth/me", token),
  documents: (token: string) => request<DocumentItem[]>("/documents", token),
  upload: (token: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ id: number; filename: string; status: string; message: string }>("/documents", token, {
      method: "POST",
      body: form,
    });
  },
  deleteDocument: (token: string, id: number) =>
    request<void>(`/documents/${id}`, token, { method: "DELETE" }),
  conversations: (token: string) => request<Conversation[]>("/conversations", token),
  conversation: (token: string, id: number) => request<ConversationDetail>(`/conversations/${id}`, token),
  deleteConversation: (token: string, id: number) =>
    request<void>(`/conversations/${id}`, token, { method: "DELETE" }),
  chat: (token: string, question: string, conversationId: number | null, documentIds: number[] | null) =>
    request<{ answer: string; conversation_id: number; sources: Source[] }>("/chat", token, {
      method: "POST",
      body: JSON.stringify({
        question,
        conversation_id: conversationId,
        document_ids: documentIds?.length ? documentIds : null,
      }),
    }),
};
