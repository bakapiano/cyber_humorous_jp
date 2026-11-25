import { LoginManager } from './manager.js';
import type { ConnectionStatus, SecretState, Vault } from './types.js';

export class MemoryVault implements Vault {
  saved: SecretState | null = null;
  read(): Promise<SecretState | null> {
    return Promise.resolve(this.saved);
  }
  write(value: SecretState): Promise<void> {
    this.saved = { ...value };
    return Promise.resolve();
  }
}
export async function testLogin(clock: () => number = Date.now) {
  const vault = new MemoryVault();
  const views: ConnectionStatus[] = [];
  const manager = new LoginManager(
    vault,
    (status) => {
      views.push(status);
      return Promise.resolve();
    },
    { current: () => Promise.resolve(null) },
    clock,
  );
  manager.probe = () =>
    Promise.resolve({ account_name: 'Synthetic account', homepage_count: 2 });
  await manager.initialize();
  return { manager, vault, views };
}
