import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import AutoImport from "unplugin-auto-import/vite";
import path from "path";

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({ imports: ["vue", "vue-router"], dts: false }),
  ],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    port: 8299,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:9199",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
