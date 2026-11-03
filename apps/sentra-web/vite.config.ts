/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// `npm run dev:mock` = `vite --mode mock` → src/api/client.ts membaca src/mocks/*.json.
// C-17: `npm run dev` mem-proxy /api ke sentra-api lokal; `npm run dev:cf` ke sentra-api di CF.
// URL lain lewat env SENTRA_API_URL (hanya dibaca Vite di mesin dev, tidak masuk bundle).
// Browser tetap memanggil origin dev server, jadi cookie sesi sentra_session berlaku tanpa CORS.
const LOCAL_API_URL = 'http://localhost:8000';
const CF_API_URL = 'https://sentra-api.cfapps.ap10.hana.ondemand.com';
// EP-18 chat sinkron sampai 150 detik, EP-26 Run checks lebih dari 150 detik: proxy jangan memutus lebih dulu.
const PROXY_TIMEOUT_MS = 200_000;

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.SENTRA_API_URL || (mode === 'cf' ? CF_API_URL : LOCAL_API_URL);

  return {
    plugins: [react()],
    base: '/',
    server: {
      proxy: {
        '/api': {
          target,
          changeOrigin: true,
          secure: true,
          timeout: PROXY_TIMEOUT_MS,
          proxyTimeout: PROXY_TIMEOUT_MS,
        },
      },
    },
    test: {
      environment: 'jsdom',
      maxWorkers: 2,
      setupFiles: ['./src/test/setup.ts'],
      env: { VITE_USE_MOCKS: 'true' },
    },
  };
});
