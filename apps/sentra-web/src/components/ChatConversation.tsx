import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useSendChat } from '../api/hooks';
import type { ChatCitation, Level, RecommendationOptionCode } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { Button } from './Button';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  citations?: ChatCitation[];
  validated?: boolean;
}

interface StoredConversation {
  chat_session_id: string | null;
  messages: ChatMessage[];
}

/** Level and recommended option of the case, so the PRD FR-16 example questions name what this case really has. */
export interface ChatCaseContext {
  level: Level;
  recommendedOption: RecommendationOptionCode | null | undefined;
}

// PRD FR-16 examples. The case questions follow the case data (e.g. `Why is this case HIGH?`, `Why did you recommend Option A?`
// for a HIGH case recommending A); the global chat has no case, so it only offers the general questions.
export function suggestedQuestions(caseContext: ChatCaseContext | undefined, whatIf: boolean): string[] {
  const questions = [
    ...(caseContext ? [`Why is this case ${caseContext.level}?`] : []),
    'Which sales orders are at risk?',
    'Which country has the highest PO exposure?',
    ...(caseContext?.recommendedOption ? [`Why did you recommend Option ${caseContext.recommendedOption}?`] : []),
  ];
  return whatIf ? [...questions, 'What if one of our supplier locations is disrupted?'] : questions;
}

const EMPTY_CONVERSATION: StoredConversation = { chat_session_id: null, messages: [] };

function conversationStorageKey(username: string, caseId: string | null): string {
  const scope = caseId ? `case-${encodeURIComponent(caseId)}` : 'global';
  return `sentra:chat:${encodeURIComponent(username)}:${scope}`;
}

function readConversation(storageKey: string): StoredConversation {
  if (typeof window === 'undefined') return { ...EMPTY_CONVERSATION, messages: [] };
  try {
    const raw = window.sessionStorage.getItem(storageKey);
    if (!raw) return { ...EMPTY_CONVERSATION, messages: [] };
    const parsed = JSON.parse(raw) as Partial<StoredConversation>;
    const messages = Array.isArray(parsed.messages)
      ? parsed.messages.filter((message): message is ChatMessage => (
        typeof message === 'object'
        && message !== null
        && (message.role === 'user' || message.role === 'assistant')
        && typeof message.content === 'string'
      ))
      : [];
    return {
      chat_session_id: typeof parsed.chat_session_id === 'string' ? parsed.chat_session_id : null,
      messages,
    };
  } catch {
    return { ...EMPTY_CONVERSATION, messages: [] };
  }
}

function writeConversation(storageKey: string, conversation: StoredConversation): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(storageKey, JSON.stringify(conversation));
  } catch {
    // The conversation still works for this page view if browser storage is unavailable.
  }
}

function safeHttpUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

function CitationList({ citations }: { citations: ChatCitation[] }) {
  if (citations.length === 0) return null;

  return (
    <div className="chat-message__sources">
      <span className="chat-message__sources-label">Sources</span>
      <ul>
        {citations.map((citation, index) => {
          // EP-18 sends the URL of a `url` citation in `id`; `url` is kept for older mock data.
          const target = citation.url ?? citation.id;
          const href = citation.type === 'url' && target ? safeHttpUrl(target) : null;
          const label = citation.type === 'record'
            ? `Record ${citation.id ?? 'source'}`
            : citation.url ?? citation.id ?? 'Source';
          return (
            <li key={`${citation.type}-${citation.id ?? citation.url ?? index}`}>
              {href ? <a href={href} target="_blank" rel="noreferrer">{label}</a> : <span>{label}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ChatThread({
  caseId,
  storageKey,
  questions,
}: {
  caseId: string | null;
  storageKey: string;
  questions: string[];
}) {
  const [conversation, setConversation] = useState(() => readConversation(storageKey));
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const sendChat = useSendChat();
  const conversationRef = useRef(conversation);
  const sendingRef = useRef(false);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' });
  }, [conversation.messages, sendChat.isPending]);

  function updateConversation(update: (current: StoredConversation) => StoredConversation) {
    const next = update(conversationRef.current);
    conversationRef.current = next;
    writeConversation(storageKey, next);
    setConversation(next);
  }

  async function sendMessage(value: string) {
    const message = value.trim();
    if (!message || sendingRef.current) return;

    sendingRef.current = true;
    setError('');
    setDraft('');
    updateConversation((current) => ({
      ...current,
      messages: [...current.messages, { role: 'user', content: message }],
    }));

    try {
      const response = await sendChat.mutateAsync({
        case_id: caseId,
        message,
        chat_session_id: conversation.chat_session_id,
      });
      updateConversation((current) => ({
        chat_session_id: response.chat_session_id,
        messages: [...current.messages, {
          role: 'assistant',
          content: response.answer,
          citations: response.citations,
          validated: response.validated,
        }],
      }));
    } catch {
      setError('SENTRA could not answer right now. Please try again.');
    } finally {
      sendingRef.current = false;
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void sendMessage(draft);
  }

  function startNewConversation() {
    if (sendChat.isPending) return;
    const next = { ...EMPTY_CONVERSATION, messages: [] };
    updateConversation(() => next);
    setError('');
  }

  return (
    <div className="chat-conversation">
      <div className="chat-conversation__toolbar">
        <span>{caseId ? `Case ${caseId}` : 'All SENTRA data'}</span>
        {conversation.messages.length > 0 && (
          <button
            type="button"
            className="chat-conversation__new"
            onClick={startNewConversation}
            disabled={sendChat.isPending}
          >
            New chat
          </button>
        )}
      </div>

      <div className="chat-conversation__messages" role="log" aria-label="Chat conversation" aria-live="polite">
        {conversation.messages.length === 0 ? (
          <div className="chat-conversation__welcome">
            <span className="orbit" aria-hidden="true" />
            <div>
              <h3>Ask SENTRA</h3>
              <p>Get answers about cases, schedules and supply risk.</p>
            </div>
            <div className="chat-conversation__suggestions" aria-label="Suggested questions">
              {questions.map((question) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => void sendMessage(question)}
                  disabled={sendChat.isPending}
                >
                  {question}
                </button>
              ))}
            </div>
          </div>
        ) : (
          conversation.messages.map((message, index) => (
            <article className={`chat-message chat-message--${message.role}`} key={`${message.role}-${index}`}>
              <span className="chat-message__role">{message.role === 'user' ? 'You' : 'SENTRA'}</span>
              <p className="chat-message__content">{message.content}</p>
              {message.role === 'assistant' && message.citations && <CitationList citations={message.citations} />}
            </article>
          ))
        )}

        {sendChat.isPending && (
          <div className="chat-conversation__typing" role="status" aria-live="polite">
            <span className="chat-conversation__typing-dots" aria-hidden="true"><i /><i /><i /></span>
            SENTRA is typing...
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {error && <p className="chat-conversation__error" role="alert">{error}</p>}

      <form className="chat-conversation__composer" onSubmit={handleSubmit}>
        <input
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask a question..."
          aria-label="Chat message"
          disabled={sendChat.isPending}
        />
        <Button type="submit" loading={sendChat.isPending} disabled={!draft.trim()}>
          Send
        </Button>
      </form>
    </div>
  );
}

export function ChatConversation({ caseId, caseContext }: { caseId: string | null; caseContext?: ChatCaseContext }) {
  const { me } = useAuth();
  const username = me?.username ?? 'anonymous';
  const storageKey = conversationStorageKey(username, caseId);

  return (
    <ChatThread
      key={storageKey}
      caseId={caseId}
      storageKey={storageKey}
      questions={suggestedQuestions(caseId ? caseContext : undefined, Boolean(me?.feature_flags.whatif))}
    />
  );
}
