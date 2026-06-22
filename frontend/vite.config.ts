import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// https://vite.dev/config/
// Tailwind v4 via the official Vite plugin (replaces the v3 PostCSS wiring).
// `@` resolves to ./src so Untitled UI components' `@/...` imports work.
export default defineConfig({
  plugins: [react(), tailwindcss()],
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
