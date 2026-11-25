import { defineConfig } from 'vite';
import { resolve } from 'node:path';
import { EXTENSION_KEY } from '@youmo/browser-bridge';

export default defineConfig({
  base: './',
  build: {
    rollupOptions: {
      input: {
        connect: resolve(import.meta.dirname, 'connect.html'),
        background: resolve(import.meta.dirname, 'src/background.ts'),
      },
      output: { entryFileNames: '[name].js', chunkFileNames: 'chunks/[name].js' },
    },
  },
  plugins: [
    {
      name: 'extension-manifest',
      generateBundle() {
        this.emitFile({
          type: 'asset',
          fileName: 'manifest.json',
          source: JSON.stringify(
            {
              manifest_version: 3,
              name: 'Youmo · YouTube 本地连接',
              version: '0.1.0',
              description:
                '仅在主动配对和授权后，把当前专用 profile 的 YouTube 会话同步到本机工作流。',
              key: EXTENSION_KEY,
              minimum_chrome_version: '120',
              incognito: 'not_allowed',
              permissions: ['cookies', 'storage', 'alarms'],
              host_permissions: ['https://*.youtube.com/*', 'http://127.0.0.1/*'],
              externally_connectable: {
                matches: ['http://127.0.0.1/*', 'http://localhost/*'],
              },
              background: { service_worker: 'background.js', type: 'module' },
              action: {
                default_popup: 'connect.html',
                default_title: '连接 YouTube 工作流',
              },
              content_security_policy: {
                extension_pages:
                  "script-src 'self'; object-src 'none'; connect-src http://127.0.0.1:8767",
              },
            },
            null,
            2,
          ),
        });
      },
    },
  ],
});
