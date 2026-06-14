import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'node:path';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    target: 'es2020',
    outDir: path.resolve(__dirname, '../src/scifinder_route_mcp/admin_webui'),
    emptyOutDir: false,
    assetsDir: 'assets'
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8001'
    }
  }
});
