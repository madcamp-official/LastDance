import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  optimizeDeps: {
    // react-katex(CJS/UMD)는 사전번들링이 제공하는 CJS->ESM 변환이 필요해서 제외하면 안 됨.
    // katex만 제외 — 사전번들링(rolldown) 과정에서 KaTeX 토크나이저 정규식이 손상되는 문제 회피.
    exclude: ['katex'],
  },
})
