import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 개발 서버가 /v1 을 라우터로 넘긴다. 같은 출처로 보이므로 개발 중에는 CORS 설정이 필요 없다.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": { target: process.env.ROUTER_URL ?? "http://localhost:8000", changeOrigin: true },
    },
  },
});
