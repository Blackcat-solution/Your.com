export type ApprovalKey = { privateKey: CryptoKey; publicKey: string };

const DATABASE_NAME = "nexusrelay-approval-keys";
const STORE_NAME = "keys";
const RECORD_KEY = "active";

export function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function openKeyStore(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) request.result.createObjectStore(STORE_NAME);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function readStoredKey(db: IDBDatabase): Promise<ApprovalKey | undefined> {
  return new Promise((resolve, reject) => {
    const request = db.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).get(RECORD_KEY);
    request.onsuccess = () => resolve(request.result as ApprovalKey | undefined);
    request.onerror = () => reject(request.error);
  });
}

function writeKey(db: IDBDatabase, key: ApprovalKey): Promise<void> {
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).put(key, RECORD_KEY);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

async function createApprovalKey(): Promise<ApprovalKey> {
  // WebCrypto can only export a pair generated as extractable, so the pair is
  // created extractable, the public bytes are exported once, and the private key
  // is re-imported as non-extractable before it is stored in IndexedDB.
  const pair = (await crypto.subtle.generateKey({ name: "Ed25519" } as Algorithm, true, [
    "sign",
    "verify",
  ])) as CryptoKeyPair;
  const publicKey = toBase64(new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey)));
  const pkcs8 = await crypto.subtle.exportKey("pkcs8", pair.privateKey);
  const privateKey = await crypto.subtle.importKey("pkcs8", pkcs8, { name: "Ed25519" } as Algorithm, false, [
    "sign",
  ]);
  return { privateKey, publicKey };
}

export async function getOrCreateApprovalKey(): Promise<ApprovalKey> {
  const db = await openKeyStore();
  try {
    const stored = await readStoredKey(db);
    if (stored) return stored;
    const created = await createApprovalKey();
    await writeKey(db, created);
    return created;
  } finally {
    db.close();
  }
}
