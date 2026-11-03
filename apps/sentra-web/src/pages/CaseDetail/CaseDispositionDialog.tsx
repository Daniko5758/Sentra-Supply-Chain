import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { Button } from '../../components/Button';

export type CaseDisposition = 'close' | 'dismiss';

interface CaseDispositionDialogProps {
  action: CaseDisposition;
  /** Case shown in the dialog, e.g. `CASE-0001 · Flood – Karawang`. */
  caseLabel: string;
  /** Button that opened the dialog; focus returns to it when the dialog closes. */
  returnFocusTo: HTMLElement | null;
  busy: boolean;
  onCancel: () => void;
  /** Rejects (throws) when the server refuses; the dialog then stays open with the message. */
  onConfirm: (comment: string) => Promise<void>;
}

const FOCUSABLE = 'button:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

// EP-19: close = comment optional (READY_FOR_DECISION or EXECUTED); dismiss = comment required (any open status).
const COPY: Record<CaseDisposition, { title: string; description: string; label: string; confirm: string }> = {
  close: {
    title: 'Close case',
    description: 'The case leaves the list of open cases. A comment is optional and is kept in the case trace.',
    label: 'Comment (optional)',
    confirm: 'Close case',
  },
  dismiss: {
    title: 'Dismiss case',
    description: 'The case is marked as dismissed and leaves the list of open cases. A comment is required and is kept in the case trace.',
    label: 'Dismissal comment',
    confirm: 'Dismiss',
  },
};

export function CaseDispositionDialog({ action, caseLabel, returnFocusTo, busy, onCancel, onConfirm }: CaseDispositionDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const commentId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [comment, setComment] = useState('');
  const [error, setError] = useState('');
  const copy = COPY[action];
  const trimmed = comment.trim();
  const commentRequired = action === 'dismiss';

  useEffect(() => {
    textareaRef.current?.focus();
    return () => {
      if (returnFocusTo?.isConnected) returnFocusTo.focus();
    };
  }, [returnFocusTo]);

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.stopPropagation();
      if (!busy) onCancel();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function submit() {
    if ((commentRequired && !trimmed) || busy) return;
    setError('');
    try {
      await onConfirm(trimmed);
    } catch (confirmError) {
      setError(confirmError instanceof Error ? confirmError.message : `The case could not be ${action === 'close' ? 'closed' : 'dismissed'}.`);
    }
  }

  return createPortal(
    <div className="case-disposition-dialog__backdrop">
      <div
        ref={dialogRef}
        className="case-disposition-dialog card"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onKeyDown={handleKeyDown}
      >
        <h2 id={titleId}>{copy.title}</h2>
        <p id={descriptionId}>{caseLabel}. {copy.description}</p>
        <label htmlFor={commentId}>{copy.label}</label>
        <textarea
          id={commentId}
          ref={textareaRef}
          value={comment}
          rows={4}
          maxLength={1000}
          required={commentRequired}
          aria-required={commentRequired}
          onChange={(event) => setComment(event.target.value)}
        />
        {error && <p className="case-disposition-dialog__error" role="alert">{error}</p>}
        <div className="case-disposition-dialog__actions">
          <Button variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button
            variant={action === 'dismiss' ? 'danger' : 'primary'}
            onClick={() => void submit()}
            loading={busy}
            disabled={commentRequired && !trimmed}
          >
            {copy.confirm}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
