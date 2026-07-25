import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const uvsockPort = Number(env.UVSC_PORT ?? 35876);
  const viewerPort = Number(env.VIEWER_PORT ?? uvsockPort + 10);
  const backend = `http://127.0.0.1:${viewerPort}`;

  return {
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": backend,
        "/ws": {
          target: backend,
          ws: true,
        },
      },
    },
  };
});
