// Kotak pilihan dengan ketik-untuk-mencari (combobox ARIA 1.2, tanpa pustaka baru). Setiap kata yang diketik harus ada di
// teks pilihan (label, detail, atau kata kunci tambahan), tidak peduli huruf besar/kecil dan urutan kata.
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import './search-select.css';

export interface SearchSelectOption {
  value: string;
  /** Teks utama; juga tampil di kotak setelah dipilih. */
  label: string;
  /** Baris kedua di daftar (mis. jumlah, tanggal, supplier). */
  detail?: string;
  /** Teks tambahan yang ikut dicari tetapi tidak ditampilkan (mis. ID tanpa spasi). */
  keywords?: string;
  /** Label kecil di samping teks utama (mis. `This case`); ikut dicari. */
  tag?: string;
}

interface SearchSelectProps {
  label: string;
  value: string;
  options: SearchSelectOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  /** Jumlah baris yang dirender sekaligus; sisanya muncul setelah pencarian dipersempit. */
  limit?: number;
  className?: string;
  /** Kotak ditandai merah (mis. wajib diisi tetapi kosong). */
  invalid?: boolean;
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/\s*\/\s*/g, '/').replace(/\s+/g, ' ').trim();
}

function matches(option: SearchSelectOption, words: string[]): boolean {
  if (words.length === 0) return true;
  const haystack = normalize(`${option.label} ${option.detail ?? ''} ${option.keywords ?? ''} ${option.tag ?? ''} ${option.value}`);
  return words.every((word) => haystack.includes(word));
}

/** Bagian teks yang cocok dengan kata pencarian ditebalkan. */
function highlight(text: string, words: string[]): ReactNode {
  if (words.length === 0) return text;
  const lower = text.toLowerCase();
  const marks: [number, number][] = [];
  for (const word of words) {
    let from = 0;
    for (;;) {
      const at = lower.indexOf(word, from);
      if (at < 0) break;
      marks.push([at, at + word.length]);
      from = at + word.length;
    }
  }
  if (marks.length === 0) return text;
  marks.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const mark of marks) {
    const last = merged[merged.length - 1];
    if (last && mark[0] <= last[1]) last[1] = Math.max(last[1], mark[1]);
    else merged.push([...mark]);
  }
  const parts: ReactNode[] = [];
  let cursor = 0;
  merged.forEach(([start, end], index) => {
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(<mark key={index}>{text.slice(start, end)}</mark>);
    cursor = end;
  });
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}

export function SearchSelect({
  label, value, options, onChange, placeholder = 'Type to search…', limit = 50, className, invalid = false,
}: SearchSelectProps) {
  const id = useId();
  const inputId = `${id}-input`;
  const listId = `${id}-list`;
  const selected = options.find((option) => option.value === value);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  // While the box has focus it always holds the search text; the chosen label shows only when it is not focused,
  // so typing after a choice starts a new search instead of adding to the label.
  const [focused, setFocused] = useState(false);
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);
  const words = useMemo(() => normalize(query).split(' ').filter(Boolean), [query]);
  const found = useMemo(() => options.filter((option) => matches(option, words)), [options, words]);
  const shown = found.slice(0, limit);

  useEffect(() => {
    setActive(0);
  }, [query, open]);

  useEffect(() => {
    if (!open) return;
    const row = listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    if (row && typeof row.scrollIntoView === 'function') row.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  function choose(option: SearchSelectOption) {
    onChange(option.value);
    setQuery('');
    setOpen(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (!open) setOpen(true);
      else setActive((current) => Math.min(current + 1, shown.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((current) => Math.max(current - 1, 0));
    } else if (event.key === 'Enter') {
      if (open && shown[active]) {
        event.preventDefault();
        choose(shown[active]);
      }
    } else if (event.key === 'Escape') {
      if (open) {
        event.preventDefault();
        setOpen(false);
        setQuery('');
      }
    }
  }

  const activeId = open && shown[active] ? `${id}-option-${active}` : undefined;

  return (
    <div className={`search-select${open ? ' search-select--open' : ''}${invalid ? ' search-select--invalid' : ''}${className ? ` ${className}` : ''}`}>
      <label htmlFor={inputId} className="search-select__label">{label}</label>
      <div className="search-select__control">
        <svg className="search-select__icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>
        <input
          id={inputId}
          type="text"
          role="combobox"
          autoComplete="off"
          spellCheck={false}
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-invalid={invalid}
          aria-activedescendant={activeId}
          placeholder={selected ? selected.label : placeholder}
          value={focused ? query : selected?.label ?? ''}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onFocus={() => {
            setFocused(true);
            setOpen(true);
          }}
          onClick={() => setOpen(true)}
          onBlur={() => {
            setFocused(false);
            setOpen(false);
            setQuery('');
          }}
          onKeyDown={onKeyDown}
        />
        {selected && !focused && (
          <button type="button" className="search-select__clear" aria-label={`Clear ${label}`}
            onMouseDown={(event) => event.preventDefault()} onClick={() => onChange('')}>×</button>
        )}
      </div>
      {selected && (selected.detail || selected.tag) && !open && (
        <span className="search-select__selected-detail">
          {selected.tag && <span className="search-select__tag">{selected.tag}</span>}
          {selected.detail}
        </span>
      )}
      {open && (
        <div className="search-select__popover">
          <p className="search-select__count" aria-live="polite">
            {found.length === 0
              ? 'No matches'
              : found.length > shown.length
                ? `Showing ${shown.length} of ${found.length} — keep typing to narrow the list`
                : `${found.length} match${found.length === 1 ? '' : 'es'}`}
          </p>
          <ul id={listId} role="listbox" aria-label={label} ref={listRef} className="search-select__list">
            {shown.map((option, index) => (
              <li
                key={option.value}
                id={`${id}-option-${index}`}
                data-index={index}
                role="option"
                aria-selected={option.value === value}
                className={`search-select__option${index === active ? ' search-select__option--active' : ''}${option.value === value ? ' search-select__option--selected' : ''}`}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setActive(index)}
                onClick={() => choose(option)}
              >
                <span className="search-select__option-label">
                  {highlight(option.label, words)}
                  {option.tag && <span className="search-select__tag">{option.tag}</span>}
                </span>
                {option.detail && <span className="search-select__option-detail">{highlight(option.detail, words)}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
