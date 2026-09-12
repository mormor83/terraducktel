/** Subset of vscode.SecretStorage so the token manager is testable without the editor. */
export interface SecretStore {
  get(key: string): Promise<string | undefined>;
  store(key: string, value: string): Promise<void>;
  delete(key: string): Promise<void>;
}
export class MemorySecretStore implements SecretStore {
  private m = new Map<string, string>();
  async get(k: string) { return this.m.get(k); }
  async store(k: string, v: string) { this.m.set(k, v); }
  async delete(k: string) { this.m.delete(k); }
}
