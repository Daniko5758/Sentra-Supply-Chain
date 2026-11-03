import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { Button } from '../../components/Button';

interface RejectDialogProps {
  /** Aksi yang ditolak, mis. `1. Change purchase order date · Purchase order 4500001001 / Item 10`. */
  actionLabel: string;
  /** Tombol `Reject…` yang membuka dialog; fokus kembali ke sini saat dialog ditutup. */
  returnFocusTo: HTMLElement | null;
  busy: boolean;
  onCancel: () => void;
  /** Menolak (throw) bila server gagal; dialog lalu tetap terbuka dengan pesan error. */
  onConfirm: (comment: string) => Promise<void>;
}

const FOCUSABLE = 'button:not([disabled]), textarea:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

export function RejectDialog({ actionLabel, returnFocusTo, busy, onCancel, onConfirm }: RejectDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const commentId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [comment, setComment] = useState(''); // terhapus saat dialog ditutup (komponen di-unmount)
  const [error, setError] = useState('');
  const trimmed = comment.trim();

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
    // Fokus terkunci di dalam dialog.
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
    if (!trimmed || busy) return;
    setError('');
    try {
      await onConfirm(trimmed);
    } catch (rejectError) {
      setError(rejectError instanceof Error ? rejectError.message : 'The ERP action could not be rejected.');
    }
  }

  return createPortal(
    <div className="reject-dialog__backdrop">
      <div
        ref={dialogRef}
        className="reject-dialog card"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onKeyDown={handleKeyDown}
      >
        <h2 id={titleId}>Reject ERP action</h2>
        <p id={descriptionId}>
          {actionLabel}. The action will not be sent to the ERP. Your comment is kept in the case trace.
        </p>
        <label htmlFor={commentId}>Rejection comment</label>
        <textarea
          id={commentId}
          ref={textareaRef}
          value={comment}
          rows={4}
          required
          aria-required="true"
          onChange={(event) => setComment(event.target.value)}
        />
        {error && <p className="approval-message approval-message--error" role="alert">{error}</p>}
        <div className="reject-dialog__actions">
          <Button variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant="danger" onClick={() => void submit()} loading={busy} disabled={!trimmed}>Reject</Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
