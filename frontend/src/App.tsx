import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent, type DragEvent } from "react";
import {
  AlertTriangle, ArrowUp, BookOpen, Check, ChevronDown, ChevronRight, CircleHelp,
  FileText, FileUp, LoaderCircle, LogOut, MessageSquare, Moon, MoreHorizontal, Plus, Search,
  RefreshCw, Send, Settings2, ShieldCheck, Sparkles, Sun, Trash2, UploadCloud, X,
} from "lucide-react";
import { api, ApiError, type Conversation, type DocumentItem, type Message, type Source, type User } from "./lib/api";

type Section = "chat" | "documents";
const TOKEN_KEY = "noted_access_token";
type FailedRequest = { question: string; messageId: number | string; status: number | null; detail: string };

function formatDate(value?: string | null) {
  if (!value) return "Just now";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Recently" : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(date);
}
function formatType(type: string) { return type.toUpperCase().replace(".", ""); }
function statusTone(status: string) {
  const normalized = status.toLowerCase();
  if (normalized === "completed") return "text-emerald-300 bg-emerald-400/10";
  if (normalized === "failed") return "text-rose-300 bg-rose-400/10";
  return "text-amber-200 bg-amber-300/10";
}
function readableError(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

export default function App() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [section, setSection] = useState<Section>("chat");
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [selectedDocs, setSelectedDocs] = useState<number[]>([]);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [failedRequest, setFailedRequest] = useState<FailedRequest | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState("");
  const [error, setError] = useState("");
  const [dark, setDark] = useState(() => localStorage.getItem("noted_theme") !== "light");
  const [search, setSearch] = useState("");
  const [mobileNav, setMobileNav] = useState(false);
  const [userMenu, setUserMenu] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const chatEnd = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);

  const refreshWorkspace = useCallback(async (accessToken: string) => {
    const [docList, conversationList] = await Promise.all([
      api.documents(accessToken),
      api.conversations(accessToken),
    ]);
    setDocuments(docList);
    setConversations(conversationList);
  }, []);

  useEffect(() => {
    if (!token) { setLoading(false); return; }
    let mounted = true;
    Promise.all([api.me(token), api.documents(token), api.conversations(token)])
      .then(([profile, docList, conversationList]) => {
        if (!mounted) return;
        setUser(profile);
        setDocuments(docList);
        setConversations(conversationList);
      })
      .catch((reason: unknown) => {
        if (!mounted) return;
        if (reason instanceof ApiError && reason.status === 401) {
          localStorage.removeItem(TOKEN_KEY);
          setToken(null);
          setUser(null);
        } else setAuthError(readableError(reason));
      })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, [token]);

  useEffect(() => {
    if (!token || !documents.some((document) => document.status.toLowerCase() === "pending" || document.status.toLowerCase() === "processing")) return;
    const interval = window.setInterval(() => {
      api.documents(token).then(setDocuments).catch((reason: unknown) => setError(readableError(reason)));
    }, 6000);
    return () => window.clearInterval(interval);
  }, [token, documents]);

  useEffect(() => {
    chatEnd.current?.scrollTo({ top: chatEnd.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  const filteredDocuments = useMemo(() => {
    const query = search.trim().toLowerCase();
    return query ? documents.filter((item) => item.filename.toLowerCase().includes(query)) : documents;
  }, [documents, search]);

  const openConversation = async (conversationId: number) => {
    if (!token) return;
    setError("");
    setFailedRequest(null);
    setSection("chat");
    setActiveConversation(conversationId);
    setMessages([]);
    try {
      const conversation = await api.conversation(token, conversationId);
      setMessages(conversation.messages.map((message) => ({
        ...message,
        sources: message.sources?.map((source) => ({
          document_chunk_id: source.document_chunk_id,
          chunk_id: source.document_chunk_id,
          similarity_score: source.similarity_score,
          filename: null,
          page_number: null,
        })),
      })));
    } catch (reason) { setError(readableError(reason)); }
    setMobileNav(false);
  };

  const newConversation = () => {
    setActiveConversation(null);
    setMessages([]);
    setQuestion("");
    setSection("chat");
    setFailedRequest(null);
    setError("");
    setMobileNav(false);
    window.setTimeout(() => textarea.current?.focus(), 50);
  };

  const submitQuestion = async (event?: FormEvent, retry?: FailedRequest) => {
    event?.preventDefault();
    const cleanQuestion = (retry?.question ?? question).trim();
    if (!cleanQuestion || !token || sending) return;
    const messageId = retry?.messageId ?? `pending-${Date.now()}`;
    const userMessage = retry
      ? messages.find((message) => message.id === messageId) ?? { id: messageId, role: "user" as const, content: cleanQuestion }
      : { id: messageId, role: "user" as const, content: cleanQuestion };
    if (!retry) setMessages((current) => [...current, userMessage]);
    setFailedRequest(null);
    setQuestion("");
    setSending(true);
    setError("");
    try {
      const result = await api.chat(token, cleanQuestion, activeConversation, selectedDocs);
      setActiveConversation(result.conversation_id);
      setMessages((current) => [
        ...current.filter((message) => message.id !== messageId),
        userMessage,
        { id: `answer-${Date.now()}`, role: "assistant", content: result.answer, sources: result.sources },
      ]);
      try {
        await refreshWorkspace(token);
      } catch (reason) {
        setError(`Your answer arrived, but the workspace could not refresh: ${readableError(reason)}`);
      }
    } catch (reason) {
      setFailedRequest({
        question: cleanQuestion,
        messageId,
        status: reason instanceof ApiError ? reason.status : null,
        detail: readableError(reason),
      });
    } finally { setSending(false); }
  };

  const uploadFiles = async (files: FileList | File[]) => {
    if (!token || !files.length) return;
    const allowed = Array.from(files).filter((file) => /\.(pdf|docx)$/i.test(file.name));
    const skipped = allowed.length !== files.length;
    if (!allowed.length) {
      setError("Only PDF and DOCX documents are supported.");
      return;
    }
    setUploading(true);
    setError("");
    try {
      for (const file of allowed) await api.upload(token, file);
      await refreshWorkspace(token);
      if (skipped) setError("Unsupported files were skipped. Only PDF and DOCX documents are supported.");
    } catch (reason) { setError(readableError(reason)); }
    finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const handleDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setDragging(false);
    void uploadFiles(event.dataTransfer.files);
  };

  const deleteDocument = async (document: DocumentItem) => {
    if (!token || !window.confirm(`Delete “${document.filename}”?`)) return;
    try {
      await api.deleteDocument(token, document.id);
      setDocuments((current) => current.filter((item) => item.id !== document.id));
      setSelectedDocs((current) => current.filter((id) => id !== document.id));
    } catch (reason) { setError(readableError(reason)); }
  };

  const deleteConversation = async (conversation: Conversation) => {
    if (!token || !window.confirm(`Delete “${conversation.title}”?`)) return;
    try {
      await api.deleteConversation(token, conversation.id);
      setConversations((current) => current.filter((item) => item.id !== conversation.id));
      if (activeConversation === conversation.id) newConversation();
    } catch (reason) { setError(readableError(reason)); }
  };

  const handleAuth = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAuthBusy(true);
    setAuthError("");
    const data = new FormData(event.currentTarget);
    const name = String(data.get("name") ?? "").trim();
    const email = String(data.get("email") ?? "").trim();
    const password = String(data.get("password") ?? "");
    try {
      if (authMode === "register") await api.register(name, email, password);
      const response = await api.login(email, password);
      localStorage.setItem(TOKEN_KEY, response.access_token);
      setToken(response.access_token);
      setLoading(true);
    } catch (reason) { setAuthError(readableError(reason)); }
    finally { setAuthBusy(false); }
  };

  const signOut = () => {
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setUser(null);
    setMessages([]);
    setDocuments([]);
    setConversations([]);
    setActiveConversation(null);
    setFailedRequest(null);
    setUserMenu(false);
  };
  const toggleTheme = () => {
    setDark((current) => {
      localStorage.setItem("noted_theme", current ? "light" : "dark");
      return !current;
    });
  };

  const handleTextareaKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitQuestion();
    }
  };

  if (loading) return <LoadingScreen />;
  if (!token || !user) return (
    <AuthScreen mode={authMode} setMode={setAuthMode} onSubmit={handleAuth} busy={authBusy} error={authError} />
  );

  return (
    <main className={`app-shell h-dvh overflow-hidden text-[#f4f3f8] ${dark ? "" : "light-theme"}`} onDragOver={(event) => event.preventDefault()} onDrop={handleDrop}>
      <div className="flex h-full min-h-0">
        {mobileNav && <button aria-label="Close navigation" onClick={() => setMobileNav(false)} className="fixed inset-0 z-30 bg-black/60 lg:hidden" />}
        <aside className={`fixed inset-y-0 left-0 z-40 flex w-[264px] flex-col border-r border-white/[0.07] bg-[#141419] transition-transform duration-300 lg:static lg:translate-x-0 ${mobileNav ? "translate-x-0" : "-translate-x-full"}`}>
          <div className="flex h-[76px] items-center justify-between px-6">
            <button onClick={newConversation} className="flex items-center gap-3">
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-violet-500/15 text-violet-300"><Sparkles size={19} /></span>
              <span className="font-display text-[17px] font-bold tracking-[-.04em]">noted<span className="text-violet-400">.</span></span>
            </button>
            <button onClick={() => setMobileNav(false)} className="rounded-lg p-2 text-[#85838e] hover:bg-white/5 lg:hidden"><X size={17} /></button>
          </div>
          <div className="px-4 pt-3">
            <button onClick={newConversation} className="flex w-full items-center justify-between rounded-xl border border-white/[0.09] bg-white/[0.035] px-3.5 py-3 text-[13px] font-medium text-[#e9e7ef] transition hover:border-violet-400/30 hover:bg-violet-400/[0.07]">
              <span className="flex items-center gap-2.5"><Plus size={16} className="text-violet-300" /> New conversation</span>
              <span className="text-[10px] text-[#777580]">⌘ K</span>
            </button>
          </div>
          <nav aria-label="Main navigation" className="space-y-1 px-3 pt-7">
            <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[.16em] text-[#686772]">Workspace</p>
            <NavButton active={section === "chat"} icon={<MessageSquare size={16} />} label="Ask anything" onClick={() => { setSection("chat"); setMobileNav(false); }} />
            <NavButton active={section === "documents"} icon={<BookOpen size={16} />} label="Your library" count={documents.length} onClick={() => { setSection("documents"); setMobileNav(false); }} />
          </nav>
          <div className="subtle-scrollbar mt-8 flex-1 overflow-y-auto px-3">
            <div className="mb-3 flex items-center justify-between px-3">
              <p className="text-[10px] font-semibold uppercase tracking-[.16em] text-[#686772]">Recent conversations</p>
              <button aria-label="New conversation" onClick={newConversation} className="rounded-md p-1 text-[#777580] hover:bg-white/5 hover:text-white"><Plus size={14} /></button>
            </div>
            {conversations.length === 0 ? (
              <p className="px-3 py-2 text-xs leading-5 text-[#777580]">Your conversations will appear here.</p>
            ) : conversations.map((conversation) => (
              <div key={conversation.id} className={`group flex items-center rounded-lg transition ${activeConversation === conversation.id && section === "chat" ? "bg-white/[0.07]" : "hover:bg-white/[0.035]"}`}>
                <button onClick={() => void openConversation(conversation.id)} className={`flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2.5 text-left text-[12px] ${activeConversation === conversation.id && section === "chat" ? "text-white" : "text-[#a6a4ad]"}`}>
                  <MessageSquare size={14} className="shrink-0 text-[#777580]" />
                  <span className="truncate">{conversation.title}</span>
                </button>
                <button aria-label={`Delete ${conversation.title}`} onClick={() => void deleteConversation(conversation)} className="mr-1 rounded p-1.5 text-[#777580] opacity-0 transition hover:text-rose-300 group-hover:opacity-100"><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
          <div className="mx-4 mb-3 rounded-xl border border-violet-400/10 bg-violet-400/[0.045] p-3.5">
            <div className="flex items-center gap-2 text-[11px] font-medium text-[#ddd8f2]"><ShieldCheck size={14} className="text-violet-300" /> Private by design</div>
            <p className="mt-1.5 text-[10px] leading-[1.6] text-[#85838e]">Your documents stay yours. Answers are grounded in your library.</p>
          </div>
          <div className="relative border-t border-white/[0.07] p-3">
            {userMenu && <div className="absolute bottom-[68px] left-3 right-3 rounded-xl border border-white/10 bg-[#222128] p-1.5 shadow-xl">
              <button onClick={toggleTheme} className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-xs text-[#c9c7d0] hover:bg-white/5">{dark ? <Sun size={15} /> : <Moon size={15} />} Switch to {dark ? "light" : "dark"} theme</button>
              <button onClick={signOut} className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-xs text-rose-300 hover:bg-rose-400/10"><LogOut size={15} /> Sign out</button>
            </div>}
            <button onClick={() => setUserMenu((value) => !value)} className="flex w-full items-center gap-3 rounded-xl p-2 text-left hover:bg-white/[0.04]">
              <Avatar name={user.name} />
              <span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-[#e8e6ed]">{user.name}</span><span className="mt-0.5 block truncate text-[10px] text-[#777580]">{user.email}</span></span>
              <MoreHorizontal size={17} className="text-[#777580]" />
            </button>
          </div>
        </aside>

        <section className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <header className="flex h-[76px] shrink-0 items-center justify-between border-b border-white/[0.06] px-5 sm:px-8">
            <div className="flex items-center gap-3">
              <button onClick={() => setMobileNav(true)} aria-label="Open navigation" className="rounded-lg p-2 text-[#aaa8b2] hover:bg-white/5 lg:hidden"><ChevronRight size={18} /></button>
              <div>
                <p className="text-[10px] font-medium uppercase tracking-[.16em] text-[#777580]">Workspace</p>
                <h1 className="mt-0.5 font-display text-[15px] font-semibold tracking-[-.02em] text-[#e8e6ed]">{section === "chat" ? activeConversation ? conversations.find((item) => item.id === activeConversation)?.title ?? "Conversation" : "New conversation" : "Your library"}</h1>
              </div>
            </div>
            <div className="flex items-center gap-2 sm:gap-3">
              <span className="hidden items-center gap-1.5 rounded-full border border-white/[0.07] bg-white/[0.025] px-3 py-1.5 text-[10px] text-[#92909a] sm:flex"><span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> Workspace ready</span>
              <a href={`${import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"}/docs`} target="_blank" rel="noreferrer" title="Open API documentation" className="rounded-lg p-2 text-[#85838e] transition hover:bg-white/5 hover:text-white"><CircleHelp size={17} /></a>
              <button onClick={toggleTheme} aria-label="Toggle theme" className="rounded-lg p-2 text-[#85838e] transition hover:bg-white/5 hover:text-white">{dark ? <Sun size={17} /> : <Moon size={17} />}</button>
            </div>
          </header>

          {error && <div role="alert" className="mx-5 mt-4 flex items-start gap-2 rounded-xl border border-rose-400/20 bg-rose-400/[0.08] px-4 py-3 text-xs text-rose-200 sm:mx-8"><span className="flex-1">{error}</span><button onClick={() => setError("")} aria-label="Dismiss error"><X size={14} /></button></div>}

          {section === "chat" ? (
            <ChatView
              messages={messages} question={question} setQuestion={setQuestion} submit={submitQuestion}
              onKeyDown={handleTextareaKey} sending={sending} documents={documents} selectedDocs={selectedDocs}
              setSelectedDocs={setSelectedDocs} textarea={textarea} chatEnd={chatEnd} onOpenLibrary={() => setSection("documents")}
              failedRequest={failedRequest}
              retryQuestion={(request) => void submitQuestion(undefined, request)}
              editFailedQuestion={(request) => {
                setMessages((current) => current.filter((message) => message.id !== request.messageId));
                setFailedRequest(null);
                setQuestion(request.question);
                window.setTimeout(() => textarea.current?.focus(), 50);
              }}
            />
          ) : (
            <LibraryView
              documents={filteredDocuments} total={documents.length} search={search} setSearch={setSearch}
              uploading={uploading} dragging={dragging} setDragging={setDragging} fileInput={fileInput}
              onUpload={uploadFiles} onDrop={handleDrop} onDelete={deleteDocument} onRefresh={() => token && refreshWorkspace(token)}
            />
          )}
        </section>
      </div>
    </main>
  );
}

function LoadingScreen() {
  return <div className="app-shell grid min-h-screen place-items-center"><div className="flex items-center gap-3 text-sm text-[#a6a4ad]"><LoaderCircle size={18} className="animate-spin text-violet-300" /> Opening your workspace…</div></div>;
}

function AuthScreen({ mode, setMode, onSubmit, busy, error }: {
  mode: "login" | "register"; setMode: (mode: "login" | "register") => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void; busy: boolean; error: string;
}) {
  return <main className="app-shell relative grid min-h-screen place-items-center overflow-hidden px-5 py-10 text-[#f4f3f8]">
    <div className="pointer-events-none absolute left-1/2 top-[-260px] h-[560px] w-[700px] -translate-x-1/2 rounded-full bg-violet-600/[0.09] blur-[110px]" />
    <div className="relative w-full max-w-[410px] rise-in">
      <div className="mb-9 flex items-center justify-center gap-3"><span className="grid h-10 w-10 place-items-center rounded-xl border border-violet-300/20 bg-violet-400/10 text-violet-200"><Sparkles size={20} /></span><span className="font-display text-xl font-bold tracking-[-.05em]">noted<span className="text-violet-400">.</span></span></div>
      <div className="surface rounded-2xl p-7 shadow-[0_24px_80px_rgba(0,0,0,.28)] sm:p-9">
        <p className="text-center text-[10px] font-semibold uppercase tracking-[.18em] text-violet-300">Your personal knowledge space</p>
        <h1 className="mt-3 text-center font-display text-2xl font-semibold tracking-[-.04em]">{mode === "login" ? "Welcome back" : "Create your workspace"}</h1>
        <p className="mt-2 text-center text-xs leading-5 text-[#8d8b96]">{mode === "login" ? "Pick up where your ideas left off." : "A calmer way to work with your documents."}</p>
        <form onSubmit={onSubmit} className="mt-7 space-y-4">
          {mode === "register" && <Field label="Name" name="name" placeholder="Your name" autoComplete="name" required />}
          <Field label="Email address" name="email" type="email" placeholder="you@example.com" autoComplete="email" required />
          <Field label="Password" name="password" type="password" placeholder={mode === "register" ? "At least 8 characters" : "Your password"} autoComplete={mode === "register" ? "new-password" : "current-password"} minLength={mode === "register" ? 8 : undefined} required />
          {error && <p role="alert" className="rounded-lg border border-rose-400/20 bg-rose-400/[0.08] px-3 py-2.5 text-xs text-rose-200">{error}</p>}
          <button disabled={busy} className="mt-1 flex w-full items-center justify-center gap-2 rounded-xl bg-violet-500 px-4 py-3 text-sm font-semibold text-white shadow-[0_6px_22px_rgba(112,79,222,.22)] transition hover:bg-violet-400 disabled:cursor-wait disabled:opacity-60">{busy && <LoaderCircle size={16} className="animate-spin" />}{mode === "login" ? "Continue to workspace" : "Create account"}<ChevronRight size={15} /></button>
        </form>
        <p className="mt-6 text-center text-xs text-[#85838e]">{mode === "login" ? "New to Noted?" : "Already have an account?"} <button onClick={() => setMode(mode === "login" ? "register" : "login")} className="font-medium text-violet-300 hover:text-violet-200">{mode === "login" ? "Create an account" : "Sign in"}</button></p>
      </div>
      <p className="mt-5 text-center text-[10px] text-[#66646f]">Your account and documents are private to you.</p>
    </div>
  </main>;
}

function Field(props: { label: string; name: string; placeholder: string; type?: string; autoComplete?: string; minLength?: number; required?: boolean }) {
  return <label className="block"><span className="mb-1.5 block text-[11px] font-medium text-[#c2c0c9]">{props.label}</span><input name={props.name} type={props.type ?? "text"} placeholder={props.placeholder} autoComplete={props.autoComplete} minLength={props.minLength} required={props.required} className="w-full rounded-xl border border-white/[0.09] bg-black/15 px-3.5 py-3 text-[13px] text-white outline-none placeholder:text-[#65636e] transition focus:border-violet-400/50 focus:ring-2 focus:ring-violet-400/10" /></label>;
}

function Avatar({ name }: { name: string }) {
  const initials = name.split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
  return <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-violet-300/15 bg-violet-400/10 text-[10px] font-semibold text-violet-200">{initials}</span>;
}

function NavButton({ active, icon, label, count, onClick }: { active: boolean; icon: React.ReactNode; label: string; count?: number; onClick: () => void }) {
  return <button onClick={onClick} className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-[12px] transition ${active ? "bg-violet-400/[0.11] font-medium text-violet-100" : "text-[#9997a1] hover:bg-white/[0.04] hover:text-[#e8e6ed]"}`}>{icon}<span className="flex-1 text-left">{label}</span>{count !== undefined && <span className={`text-[10px] ${active ? "text-violet-200/70" : "text-[#696873]"}`}>{count}</span>}</button>;
}

function ChatView({ messages, question, setQuestion, submit, onKeyDown, sending, documents, selectedDocs, setSelectedDocs, textarea, chatEnd, onOpenLibrary, failedRequest, retryQuestion, editFailedQuestion }: {
  messages: Message[]; question: string; setQuestion: (value: string) => void; submit: (event?: FormEvent) => Promise<void>;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void; sending: boolean; documents: DocumentItem[];
  selectedDocs: number[]; setSelectedDocs: (value: number[] | ((current: number[]) => number[])) => void;
  textarea: React.RefObject<HTMLTextAreaElement>; chatEnd: React.RefObject<HTMLDivElement>; onOpenLibrary: () => void;
  failedRequest: FailedRequest | null; retryQuestion: (request: FailedRequest) => void;
  editFailedQuestion: (request: FailedRequest) => void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const readyDocs = documents.filter((document) => document.status.toLowerCase() === "completed");
  const toggleDoc = (id: number) => setSelectedDocs((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);

  return <div className="relative flex min-h-0 flex-1 flex-col">
    {messages.length === 0 ? (
      <div className="subtle-scrollbar flex flex-1 flex-col items-center justify-center overflow-y-auto px-6 pb-6 pt-6 sm:px-8">
        <div className="rise-in w-full max-w-[980px]">
          <div className="mb-4 flex justify-center"><div className="relative grid h-[62px] w-[62px] place-items-center rounded-[19px] border border-violet-300/15 bg-violet-400/[0.08] text-violet-200 shadow-[0_0_44px_rgba(126,93,230,.1)]"><Sparkles size={26} /><span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border-2 border-[#101014] bg-violet-300" /></div></div>
          <p className="text-center text-[11px] font-semibold uppercase tracking-[.2em] text-violet-300">A little more clarity</p>
          <h2 className="mt-2 text-center font-display text-[44px] font-semibold leading-[1.08] tracking-[-.06em] text-[#f2f0f6] sm:text-[64px] lg:text-[72px]">Your documents,<br className="hidden sm:block" /> ready to think with.</h2>
          <p className="mx-auto mt-4 max-w-[620px] text-center text-[16px] leading-7 text-[#898792]">Ask questions, find the useful details, and get answers grounded in the things you’ve saved.</p>
          <div className="mx-auto mt-6 grid max-w-[760px] gap-3 sm:grid-cols-3">
            {[
              ["Find the essentials", "What are the key points in my documents?", "Search"],
              ["Compare details", "What deadlines or dates should I know about?", "Synthesize"],
              ["Get a quick brief", "Summarize the most important takeaways.", "Summarize"],
            ].map(([title, prompt, tag]) => <button key={title} onClick={() => setQuestion(prompt)} className="group rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 text-left transition hover:border-violet-400/25 hover:bg-violet-400/[0.04]"><span className="mb-3 flex items-center justify-between text-[11px] text-[#777580]"><span>{tag}</span><ArrowUp size={13} className="-rotate-45 transition group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-violet-300" /></span><span className="block text-[13px] font-medium text-[#cfccd6]">{title}</span><span className="mt-1.5 block text-[11px] leading-[1.6] text-[#777580]">{prompt}</span></button>)}
          </div>
          {documents.length === 0 ? <button onClick={onOpenLibrary} className="mt-4 flex items-center gap-2 rounded-full border border-white/[0.08] px-4 py-2.5 text-[12px] text-[#aaa8b2] transition hover:border-violet-400/30 hover:text-white"><FileUp size={15} className="text-violet-300" /> Add a document to get started <ChevronRight size={14} /></button> : <p className="mt-4 flex items-center justify-center gap-2 text-[11px] text-[#777580]"><span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> {documents.length} {documents.length === 1 ? "document" : "documents"} in your workspace</p>}
        </div>
        <Composer question={question} setQuestion={setQuestion} submit={submit} onKeyDown={onKeyDown} sending={sending} readyDocs={readyDocs} selectedDocs={selectedDocs} toggleDoc={toggleDoc} pickerOpen={pickerOpen} setPickerOpen={setPickerOpen} textarea={textarea} />
        <p className="mt-2 text-center text-[10px] text-[#686772]">Answers are generated from your documents and may need a second look.</p>
      </div>
    ) : <>
      <div ref={chatEnd} className="subtle-scrollbar min-h-0 flex-1 overflow-y-auto px-5 py-8 sm:px-8">
        <div className="mx-auto max-w-[760px] space-y-8">
          {messages.map((message) => <div key={message.id}><MessageBubble message={message} />{failedRequest?.messageId === message.id && <FailureNotice request={failedRequest} onRetry={retryQuestion} onEdit={editFailedQuestion} />}</div>)}
          {sending && <div className="flex gap-3 fade-in"><div className="grid h-8 w-8 shrink-0 place-items-center rounded-xl border border-violet-300/10 bg-violet-400/[0.08] text-violet-200"><Sparkles size={15} /></div><div className="space-y-2 pt-2"><div className="skeleton h-2.5 w-44 rounded-full" /><div className="skeleton h-2.5 w-64 rounded-full" /><p className="pt-1 text-[10px] text-[#777580]">Thinking through your documents…</p></div></div>}
        </div>
      </div>
      <div className="px-5 pb-5 pt-2 sm:px-8 sm:pb-7"><div className="mx-auto max-w-[760px]"><Composer question={question} setQuestion={setQuestion} submit={submit} onKeyDown={onKeyDown} sending={sending} readyDocs={readyDocs} selectedDocs={selectedDocs} toggleDoc={toggleDoc} pickerOpen={pickerOpen} setPickerOpen={setPickerOpen} textarea={textarea} /><p className="mt-2 text-center text-[9px] text-[#686772]">AI can make mistakes. Verify important details in the cited sources.</p></div></div>
    </>}
  </div>;
}

function FailureNotice({ request, onRetry, onEdit }: {
  request: FailedRequest; onRetry: (request: FailedRequest) => void; onEdit: (request: FailedRequest) => void;
}) {
  return <div role="alert" className="ml-11 mt-3 max-w-[620px] rounded-xl border border-rose-300/15 bg-rose-300/[0.06] p-4 fade-in">
    <div className="flex items-start gap-2.5">
      <AlertTriangle size={16} className="mt-0.5 shrink-0 text-rose-300" />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-rose-200">{request.status ? `Request failed · HTTP ${request.status}` : "Could not get an answer"}</p>
        {request.status === 503 && <p className="mt-1.5 text-[11px] leading-5 text-[#c7bfc3]">The API could not complete this chat request. A 503 usually points to an unavailable or misconfigured embedding/LLM provider. Check the FastAPI server log for the provider’s exact error.</p>}
        <p className="mt-1.5 break-words text-[11px] leading-5 text-[#aaa3a8]">{request.detail}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button onClick={() => onRetry(request)} className="rounded-lg bg-rose-300/15 px-3 py-1.5 text-[10px] font-medium text-rose-100 transition hover:bg-rose-300/25">Retry request</button>
          <button onClick={() => onEdit(request)} className="rounded-lg border border-white/[0.08] px-3 py-1.5 text-[10px] text-[#c7c3cc] transition hover:bg-white/[0.05]">Edit prompt</button>
        </div>
      </div>
    </div>
  </div>;
}

function Composer({ question, setQuestion, submit, onKeyDown, sending, readyDocs, selectedDocs, toggleDoc, pickerOpen, setPickerOpen, textarea }: {
  question: string; setQuestion: (value: string) => void; submit: (event?: FormEvent) => Promise<void>;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void; sending: boolean; readyDocs: DocumentItem[];
  selectedDocs: number[]; toggleDoc: (id: number) => void; pickerOpen: boolean; setPickerOpen: (value: boolean) => void;
  textarea: React.RefObject<HTMLTextAreaElement>;
}) {
  useLayoutEffect(() => {
    const input = textarea.current;
    if (!input) return;
    input.style.height = "auto";
    const height = Math.min(input.scrollHeight, 220);
    input.style.height = `${height}px`;
    input.style.overflowY = input.scrollHeight > 220 ? "auto" : "hidden";
  }, [question, textarea]);

  return <div className="relative mx-auto mt-5 w-full max-w-[780px]">
    {pickerOpen && <div className="absolute bottom-full left-0 z-20 mb-2 w-[min(330px,90vw)] overflow-hidden rounded-xl border border-white/10 bg-[#1c1b22] shadow-2xl">
      <div className="flex items-center justify-between border-b border-white/[0.07] px-3.5 py-3"><span className="text-[11px] font-medium text-[#dedbe5]">Use specific documents</span><button aria-label="Close document picker" onClick={() => setPickerOpen(false)} className="text-[#777580] hover:text-white"><X size={14} /></button></div>
      <div className="subtle-scrollbar max-h-52 overflow-y-auto p-1.5">
        {readyDocs.length === 0 ? <p className="px-3 py-3 text-xs text-[#85838e]">Upload and process a document to start asking questions.</p> : readyDocs.map((document) => <button key={document.id} onClick={() => toggleDoc(document.id)} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left hover:bg-white/[0.04]"><span className={`grid h-5 w-5 place-items-center rounded border ${selectedDocs.includes(document.id) ? "border-violet-400 bg-violet-500 text-white" : "border-white/15 text-transparent"}`}>{selectedDocs.includes(document.id) && <Check size={12} />}</span><FileText size={14} className="text-violet-300" /><span className="flex-1 truncate text-[11px] text-[#c9c7d0]">{document.filename}</span></button>)}
      </div>
      <div className="border-t border-white/[0.07] px-3 py-2 text-[9px] text-[#777580]">{selectedDocs.length ? `${selectedDocs.length} selected · no selection uses all documents` : "No selection uses all completed documents"}</div>
    </div>}
    <form onSubmit={(event) => void submit(event)} className="rounded-[18px] border border-white/[0.11] bg-[#1a191f] p-2.5 shadow-[0_10px_36px_rgba(0,0,0,.18)] transition focus-within:border-violet-300/25 focus-within:shadow-[0_10px_40px_rgba(92,69,173,.10)]">
      <textarea ref={textarea} value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={onKeyDown} rows={1} maxLength={8000} placeholder="Ask anything about your documents…" className="chat-composer w-full resize-none bg-transparent px-4 py-3 text-[14px] leading-6 text-[#eeecf2] outline-none placeholder:text-[#706e79]" />
      <div className="flex items-center justify-between px-1.5 pb-0.5">
        <button type="button" onClick={() => setPickerOpen(!pickerOpen)} className={`flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[10px] transition hover:bg-white/[0.05] ${selectedDocs.length ? "text-violet-200" : "text-[#8b8994]"}`}><BookOpen size={13} /> {selectedDocs.length ? `${selectedDocs.length} source${selectedDocs.length === 1 ? "" : "s"}` : "All documents"} <ChevronDown size={12} /></button>
        <div className="flex items-center gap-2"><span className="hidden text-[9px] text-[#66646f] sm:inline">Shift + Enter for new line</span>{question.length > 7000 && <span className="text-[9px] text-[#777580]">{question.length.toLocaleString()} / 8,000</span>}<button type="submit" disabled={!question.trim() || sending} aria-label="Send message" className="grid h-8 w-8 place-items-center rounded-[10px] bg-violet-500 text-white shadow-[0_3px_12px_rgba(112,79,222,.28)] transition hover:bg-violet-400 disabled:cursor-not-allowed disabled:bg-white/[0.07] disabled:text-[#777580] disabled:shadow-none">{sending ? <LoaderCircle size={15} className="animate-spin" /> : <Send size={14} />}</button></div>
      </div>
    </form>
  </div>;
}

function MessageBubble({ message }: { message: Message }) {
  const userMessage = message.role === "user";
  return <div className={`flex gap-3.5 rise-in ${userMessage ? "flex-row-reverse" : ""}`}>
    {userMessage ? <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-violet-300/15 bg-violet-400/10 text-[10px] font-semibold text-violet-200">You</span> : <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl border border-violet-300/10 bg-violet-400/[0.08] text-violet-200"><Sparkles size={15} /></span>}
    <div className={`min-w-0 max-w-[min(88%,650px)] ${userMessage ? "rounded-2xl rounded-tr-md border border-white/[0.07] bg-[#1d1c23] px-4 py-3" : "flex-1 pt-1"}`}>
      {!userMessage && <p className="mb-2 text-[10px] font-medium text-violet-200">Noted <span className="ml-1.5 font-normal text-[#686772]">{message.created_at ? formatDate(message.created_at) : "just now"}</span></p>}
      <div className={`message-copy text-[13px] ${userMessage ? "text-[#e6e4eb]" : "text-[#d0ced7]"}`}>{message.content}</div>
      {!userMessage && message.sources && message.sources.length > 0 && <SourceList sources={message.sources} />}
      {!userMessage && message.sources && message.sources.length === 0 && <p className="mt-3 text-[10px] text-[#777580]">No matching source passages were found in your documents.</p>}
      {!userMessage && message.sources === undefined && <p className="mt-3 text-[10px] text-[#777580]">Historical source text isn’t available from the conversation API.</p>}
    </div>
  </div>;
}

function SourceList({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState(false);
  return <div className="mt-4 max-w-[560px] overflow-hidden rounded-xl border border-white/[0.07] bg-white/[0.02]">
    <button onClick={() => setOpen((value) => !value)} aria-expanded={open} className="flex w-full items-center gap-2.5 px-3.5 py-3 text-left transition hover:bg-white/[0.025]"><span className="grid h-6 w-6 place-items-center rounded-lg bg-violet-400/[0.1] text-violet-200"><BookOpen size={12} /></span><span className="flex-1 text-[11px] font-medium text-[#cbc9d2]">Sources <span className="ml-1 text-[#777580]">{sources.length}</span></span><span className="text-[9px] text-[#777580]">Grounded in your library</span><ChevronDown size={14} className={`text-[#777580] transition-transform ${open ? "rotate-180" : ""}`} /></button>
    {open && <div className="space-y-2 border-t border-white/[0.06] p-2.5 fade-in">{sources.map((source, index) => <div key={`${source.chunk_id ?? index}-${index}`} className="rounded-lg border border-white/[0.06] bg-[#141419] p-3"><div className="flex items-start gap-2"><FileText size={14} className="mt-0.5 shrink-0 text-violet-300" /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-x-2 gap-y-1"><p className="truncate text-[10px] font-medium text-[#d2d0d9]">{source.filename || (source.document_id == null ? "Referenced passage" : `Document ${source.document_id}`)}</p><span className="text-[9px] text-[#777580]">{source.page_number == null ? "Page not available" : `Page ${source.page_number}`}</span></div><p className="mt-1 text-[9px] text-[#777580]">Relevance {Math.round(source.similarity_score * 100)}%{source.chunk_id == null ? "" : ` · Chunk ${source.chunk_id}`}</p></div></div>{source.content ? <p className="mt-2.5 border-t border-white/[0.06] pt-2.5 text-[10px] leading-[1.7] text-[#a7a5ae]">{source.content}</p> : <p className="mt-2.5 border-t border-white/[0.06] pt-2.5 text-[9px] text-[#777580]">Source preview isn’t available in conversation history.</p>}</div>)}</div>}
  </div>;
}

function LibraryView({ documents, total, search, setSearch, uploading, dragging, setDragging, fileInput, onUpload, onDrop, onDelete, onRefresh }: {
  documents: DocumentItem[]; total: number; search: string; setSearch: (value: string) => void; uploading: boolean;
  dragging: boolean; setDragging: (value: boolean) => void; fileInput: React.RefObject<HTMLInputElement>;
  onUpload: (files: FileList | File[]) => Promise<void>; onDrop: (event: DragEvent<HTMLElement>) => void;
  onDelete: (document: DocumentItem) => Promise<void>; onRefresh: () => void;
}) {
  return <div onDragEnter={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }} onDrop={onDrop} className="subtle-scrollbar flex-1 overflow-y-auto">
    <div className={`mx-auto max-w-[1030px] px-5 py-8 sm:px-8 sm:py-10 ${dragging ? "drop-active" : ""}`}>
      <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
        <div><p className="text-[10px] font-semibold uppercase tracking-[.17em] text-violet-300">Your knowledge base</p><h2 className="mt-2 font-display text-[27px] font-semibold tracking-[-.05em] text-[#f0eef4]">A home for your documents.</h2><p className="mt-2 max-w-[460px] text-[12px] leading-5 text-[#898792]">Everything you’ve added, ready to search, understand, and put to work.</p></div>
        <div className="flex gap-2"><button onClick={onRefresh} aria-label="Refresh library" className="rounded-lg border border-white/[0.08] px-3 py-2.5 text-[11px] text-[#aaa8b2] transition hover:bg-white/[0.04] hover:text-white"><RefreshCw size={14} /></button><button disabled={uploading} onClick={() => fileInput.current?.click()} className="flex items-center gap-2 rounded-lg bg-violet-500 px-3.5 py-2.5 text-[11px] font-medium text-white shadow-[0_4px_16px_rgba(112,79,222,.2)] transition hover:bg-violet-400 disabled:opacity-60">{uploading ? <LoaderCircle size={14} className="animate-spin" /> : <Plus size={15} />} Add documents</button><input ref={fileInput} type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" multiple className="hidden" onChange={(event) => event.target.files && void onUpload(event.target.files)} /></div>
      </div>
      <div className="mt-7 grid gap-3 sm:grid-cols-3">
        <Stat label="In your library" value={String(total).padStart(2, "0")} icon={<BookOpen size={15} />} />
        <Stat label="Ready to search" value={String(documents.filter((document) => document.status.toLowerCase() === "completed").length).padStart(2, "0")} icon={<Check size={15} />} />
        <Stat label="Being processed" value={String(documents.filter((document) => !["completed", "failed"].includes(document.status.toLowerCase())).length).padStart(2, "0")} icon={<LoaderCircle size={15} />} />
      </div>
      <div className="mt-9 flex flex-wrap items-center justify-between gap-3"><div><h3 className="text-[13px] font-semibold text-[#e7e5ec]">All documents</h3><p className="mt-1 text-[10px] text-[#777580]">{total} {total === 1 ? "file" : "files"} in your workspace</p></div><label className="flex w-full items-center gap-2 rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2 sm:w-[210px]"><Search size={14} className="text-[#777580]" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Find a document" className="min-w-0 flex-1 bg-transparent text-[11px] text-white outline-none placeholder:text-[#686772]" />{search && <button aria-label="Clear search" onClick={() => setSearch("")}><X size={12} className="text-[#777580]" /></button>}</label></div>
      {total > 0 && documents.length > 0 ? <div className="mt-4 overflow-hidden rounded-xl border border-white/[0.07] bg-[#17171c]">
        <div className="hidden grid-cols-[minmax(0,1fr)_110px_135px_48px] gap-4 border-b border-white/[0.06] px-4 py-3 text-[9px] font-semibold uppercase tracking-[.12em] text-[#706e79] sm:grid"><span>Document</span><span>Type</span><span>Added</span><span /></div>
        {documents.map((document) => <DocumentRow key={document.id} document={document} onDelete={onDelete} />)}
      </div> : total > 0 ? <p className="mt-5 rounded-xl border border-white/[0.07] px-4 py-10 text-center text-xs text-[#777580]">No documents match “{search}”.</p> : <div onClick={() => fileInput.current?.click()} className="mt-5 flex cursor-pointer flex-col items-center rounded-2xl border border-dashed border-white/[0.12] bg-white/[0.015] px-6 py-14 text-center transition hover:border-violet-300/30 hover:bg-violet-400/[0.025]">
        <span className="grid h-12 w-12 place-items-center rounded-2xl border border-violet-300/10 bg-violet-400/[0.07] text-violet-200"><UploadCloud size={21} /></span><p className="mt-4 text-sm font-medium text-[#dbd9e1]">Drop your files here</p><p className="mt-1.5 text-[11px] text-[#85838e]">or click to browse · PDF and DOCX</p><p className="mt-4 text-[9px] text-[#686772]">Documents are processed securely in your workspace.</p>
      </div>}
      <div className="mt-5 flex items-start gap-2 rounded-xl border border-white/[0.05] bg-white/[0.015] px-3.5 py-3"><Settings2 size={14} className="mt-0.5 shrink-0 text-[#85838e]" /><p className="text-[10px] leading-5 text-[#777580]">Documents are processed in the background. When status changes to <span className="text-emerald-300">completed</span>, you can ask questions about their contents.</p></div>
      {dragging && <div className="pointer-events-none fixed inset-4 z-50 grid place-items-center rounded-3xl border-2 border-dashed border-violet-300/50 bg-[#121117]/90 backdrop-blur-sm"><div className="text-center"><UploadCloud className="mx-auto text-violet-200" size={32} /><p className="mt-3 text-sm font-medium text-white">Drop to add to your library</p><p className="mt-1 text-xs text-[#aaa8b2]">PDF or DOCX files</p></div></div>}
    </div>
  </div>;
}

function Stat({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) {
  return <div className="flex items-center gap-3.5 rounded-xl border border-white/[0.07] bg-white/[0.02] px-4 py-3.5"><span className="grid h-8 w-8 place-items-center rounded-lg bg-violet-400/[0.08] text-violet-200">{icon}</span><div className="flex-1"><p className="text-[9px] text-[#85838e]">{label}</p><p className="mt-0.5 font-display text-[17px] font-semibold tracking-[-.03em] text-[#e5e3ea]">{value}</p></div></div>;
}

function DocumentRow({ document, onDelete }: { document: DocumentItem; onDelete: (document: DocumentItem) => Promise<void> }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const status = document.status.toLowerCase();
  return <div className="group border-b border-white/[0.045] last:border-0">
    <div className="grid items-center gap-3 px-3.5 py-3.5 transition hover:bg-white/[0.02] sm:grid-cols-[minmax(0,1fr)_110px_135px_48px] sm:gap-4 sm:px-4">
      <button onClick={() => setDetailOpen((value) => !value)} aria-expanded={detailOpen} className="flex min-w-0 items-center gap-3 text-left">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] border border-violet-300/[0.09] bg-violet-400/[0.06] text-violet-200"><FileText size={17} /></span>
        <span className="min-w-0"><span className="block truncate text-[11px] font-medium text-[#d9d7df]">{document.filename}</span><span className="mt-1 block text-[9px] text-[#777580]">{document.chunk_count} {document.chunk_count === 1 ? "chunk" : "chunks"}</span></span>
      </button>
      <span className="hidden text-[10px] text-[#9997a1] sm:block">{formatType(document.file_type)}</span>
      <span className="hidden text-[10px] text-[#9997a1] sm:block">{formatDate(document.created_at)}</span>
      <div className="flex items-center justify-between sm:justify-end"><span className={`rounded-full px-2 py-1 text-[9px] font-medium capitalize ${statusTone(status)}`}>{status}</span><div className="relative sm:ml-2"><button onClick={() => setMenuOpen((value) => !value)} aria-label={`Options for ${document.filename}`} className="rounded-md p-1.5 text-[#777580] opacity-100 hover:bg-white/5 hover:text-white sm:opacity-0 sm:group-hover:opacity-100"><MoreHorizontal size={15} /></button>{menuOpen && <><button onClick={() => setMenuOpen(false)} className="fixed inset-0 z-10 cursor-default" aria-label="Close menu" /><div className="absolute right-0 top-8 z-20 w-32 rounded-lg border border-white/10 bg-[#222128] p-1 shadow-xl"><button onClick={() => void onDelete(document)} className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-[10px] text-rose-300 hover:bg-rose-400/10"><Trash2 size={13} /> Delete file</button></div></>}</div></div>
    </div>
    {detailOpen && <div className="flex flex-wrap gap-x-6 gap-y-2 border-t border-white/[0.04] bg-black/10 px-4 py-3 pl-[60px] text-[9px] text-[#85838e] fade-in"><span>Uploaded {formatDate(document.created_at)}</span>{document.processed_at && <span>Processed {formatDate(document.processed_at)}</span>}<span>{document.chunk_count} text chunks</span>{document.processing_error && <span className="basis-full text-rose-300">Processing error: {document.processing_error}</span>}</div>}
  </div>;
}
