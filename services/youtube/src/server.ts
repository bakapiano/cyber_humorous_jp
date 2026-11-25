import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { ProxyAgent, setGlobalDispatcher } from 'undici';
import { createApp } from './app.js';
import { LiveYoutubeProvider } from './providers/live.js';
import { FileCookieSource } from './auth/legacy.js';
import { LoginManager } from './auth/manager.js';
import { WindowsVault, atomicWrite } from './auth/vault.js';

async function main() {
  const proxy = process.env.WORKFLOW_PROXY;
  if (proxy) setGlobalDispatcher(new ProxyAgent(proxy));
  const root = resolve(import.meta.dirname, '../../..');
  const tokenPath =
    process.env.WORKFLOW_INTERNAL_TOKEN_FILE ??
    resolve(root, '.secrets/youtube-service-token.txt');
  const token = (await readFile(tokenPath, 'utf8')).trim();
  const login = new LoginManager(
    new WindowsVault(resolve(root, '.secrets/youtube-session.dpapi')),
    async (status) =>
      atomicWrite(
        resolve(root, 'data/youtube-connection.json'),
        JSON.stringify({ enabled: status.status === 'connected' }),
      ),
    new FileCookieSource(process.env.YOUTUBE_COOKIE_HEADER_FILE),
  );
  await login.initialize();
  const provider = new LiveYoutubeProvider(login);
  login.probe = () => provider.probe();
  const app = createApp(provider, token, login);
  await app.listen({ host: '127.0.0.1', port: 8767 });
  process.stdout.write('YouTube adapter listening on 127.0.0.1:8767\n');
  if (await login.current()) {
    void login.verify().catch(() => {
      process.stderr.write(
        'Saved session verification did not complete; retry from the workbench.\n',
      );
    });
  }
  process.on('SIGINT', () => {
    void app.close();
  });
  process.on('SIGTERM', () => {
    void app.close();
  });
}

main().catch(() => {
  process.stderr.write(
    'YouTube service startup failed; check local token path, dependencies and port.\n',
  );
  process.exitCode = 1;
});
