import { cp, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { chromium } from '@playwright/test';
import type { TestInfo } from '@playwright/test';
import { createApp } from '../services/youtube/src/app';
import { testLogin } from '../services/youtube/src/auth/testing';
import type { YoutubeProvider } from '../services/youtube/src/contracts';

async function portScopedBuild(directory: string, address: string): Promise<void> {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) await portScopedBuild(path, address);
    else if (/\.(json|js)$/.test(entry.name)) {
      const source = await readFile(path, 'utf8');
      await writeFile(path, source.replaceAll('http://127.0.0.1:8767', address));
    }
  }
}
export async function launchExtension(testInfo: TestInfo) {
  const { manager } = await testLogin();
  const provider: YoutubeProvider = {
    home: () => Promise.resolve({ items: [], warnings: [], skipped_count: 0 }),
    video: () => Promise.reject(new Error('Offline test only')),
  };
  const app = createApp(provider, 'synthetic-internal-token-no-real-account', manager);
  const address = await app.listen({ host: '127.0.0.1', port: 0 });
  const directory = testInfo.outputPath('extension-build');
  await mkdir(directory, { recursive: true });
  await cp(resolve('extensions/youtube-login/dist'), directory, { recursive: true });
  await portScopedBuild(directory, address);
  const context = await chromium.launchPersistentContext(
    testInfo.outputPath('isolated-profile'),
    {
      channel: 'chromium',
      headless: true,
      args: [`--disable-extensions-except=${directory}`, `--load-extension=${directory}`],
    },
  );
  await context.route('https://**/*', (route) => route.abort());
  return {
    context,
    manager,
    close: async () => {
      await context.close();
      await app.close();
    },
  };
}
