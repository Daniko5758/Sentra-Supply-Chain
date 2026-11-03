import { useState } from 'react';
import { Button } from './Button';
import { ChatConversation } from './ChatConversation';
import { Icon } from './Icon';

// C-15: global chat is separate from case-specific conversations.
export function ChatPanel() {
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <span className="chat-launcher">
        <Button onClick={() => setOpen(true)}>
          <span className="chat-launcher__content">
            <Icon name="sparkle" size={16} className="chat-launcher__icon" />
            Ask SENTRA
          </span>
        </Button>
      </span>
    );
  }

  return (
    <section className="chat-panel panel-pop" aria-label="SENTRA chat">
      <div className="chat-panel__header">
        <span className="chat-panel__title">
          <span className="chat-panel__mark">
            <Icon name="sparkle" size={15} />
          </span>
          SENTRA Chat
        </span>
        <button type="button" className="chat-panel__close" aria-label="Close chat" onClick={() => setOpen(false)}>
          ×
        </button>
      </div>
      <div className="chat-panel__body">
        <ChatConversation caseId={null} />
      </div>
    </section>
  );
}
