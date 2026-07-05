import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// https://vite.dev/config/
// Tailwind v4 via the official Vite plugin (replaces the v3 PostCSS wiring).
// `@` resolves to ./src so Untitled UI components' `@/...` imports work.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        // Split the two heavyweight vendor libs into their own cacheable
        // chunks — the app code changes every release, recharts/motion don't;
        // one monolithic bundle forced a full re-download each deploy.
        manualChunks(id: string) {
          if (id.includes('node_modules/recharts') || id.includes('node_modules/d3-')) return 'charts';
          if (id.includes('node_modules/motion') || id.includes('node_modules/framer-motion')) return 'motion';
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  // Vitest — jsdom for the component/state tests; pure-logic specs (adapters,
  // confBands…) ignore the DOM but share the one runner. `css: false` skips
  // Tailwind processing so specs don't pay for the full stylesheet pipeline.
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    css: false,
  },
})
