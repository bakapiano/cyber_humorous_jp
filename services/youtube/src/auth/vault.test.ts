import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { WindowsVault } from './vault.js';

it.skipIf(process.platform !== 'win32')(
  'encrypts the synthetic session with DPAPI and survives reload',
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'youmo-vault-test-'));
    try {
      const path = join(root, 'session.bin');
      const vault = new WindowsVault(path);
      expect(await vault.read()).toBeNull();
      const secret = {
        version: 1,
        cookie: 'SAPISID=synthetic-private-value',
        grantHash: null,
        syncedAt: null,
      } as const;
      await vault.write(secret);
      expect(
        (await readFile(path)).includes(Buffer.from('synthetic-private-value')),
      ).toBe(false);
      expect(await new WindowsVault(path).read()).toEqual(secret);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  },
  20000,
);
