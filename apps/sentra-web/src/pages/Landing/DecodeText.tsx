import { useEffect, useMemo, useState } from 'react';
import { animate, useReducedMotion } from 'motion/react';

// Glyphs yang dipakai saat huruf belum "terbaca": campuran garis dan angka, seperti sinyal mentah.
const NOISE = '▪░/\\|+·01<>=~';
const TICK_MS = 70;

/** Pilihan glyph yang stabil per (huruf, tick) tanpa Math.random, agar tidak berkedip tiap render. */
function noiseGlyph(index: number, tick: number) {
  const hash = Math.imul(index + 1, 2654435761) ^ Math.imul(tick + 7, 40503);
  return NOISE[Math.abs(hash) % NOISE.length];
}

type DecodeTextProps = {
  text: string;
  /** Jeda sebelum urai dimulai (ms). */
  delay?: number;
  /** Lama seluruh teks terurai (ms). */
  duration?: number;
  className?: string;
};

/**
 * Teks yang muncul sebagai "noise" lalu terurai huruf demi huruf menjadi jelas.
 * Huruf asli selalu ada di DOM (hanya transparan sebelum terurai) sehingga ukuran, kerning,
 * dan nama aksesibel tidak berubah; glyph noise digambar CSS (`::after` dari `data-noise`),
 * jadi tidak pernah masuk isi teks.
 */
export function DecodeText({ text, delay = 0, duration = 900, className }: DecodeTextProps) {
  const reduceMotion = useReducedMotion();
  const [progress, setProgress] = useState(reduceMotion ? 1 : 0);
  const chars = useMemo(() => [...text], [text]);

  useEffect(() => {
    if (reduceMotion) {
      setProgress(1);
      return;
    }
    const controls = animate(0, 1, {
      delay: delay / 1000,
      duration: duration / 1000,
      ease: 'easeOut',
      onUpdate: setProgress,
    });
    return () => controls.stop();
  }, [reduceMotion, delay, duration]);

  const done = progress >= 1;
  const tick = Math.floor((progress * duration) / TICK_MS);
  const lastIndex = Math.max(1, chars.length - 1);

  return (
    <span className={className} data-decoded={done ? 'true' : 'false'}>
      {chars.map((char, index) => {
        // Spasi tetap teks biasa (bukan span) agar nama aksesibel dan titik pemenggalan baris tidak berubah.
        if (char === ' ') return ' ';
        // Huruf di depan terurai lebih dulu; 0.12–0.92 menyisakan ujung untuk huruf terakhir.
        const resolved = done || char === ' ' || progress >= 0.12 + 0.8 * (index / lastIndex);
        return (
          <span
            className="decode-char"
            data-resolved={resolved ? 'true' : 'false'}
            data-noise={resolved ? undefined : noiseGlyph(index, tick)}
            key={`${index}-${char}`}
          >
            {char}
          </span>
        );
      })}
    </span>
  );
}
