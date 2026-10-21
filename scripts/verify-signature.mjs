import { createHash, createPublicKey, verify } from 'node:crypto';

function decode(value) {
  const text = value.trim();
  if (!text || !/^[A-Za-z0-9+/]+={0,2}$/.test(text) || text.length % 4 !== 0) throw new Error('Invalid signature encoding');
  return Buffer.from(text,'base64');
}

// Tauri wraps a Minisign text document in base64. This release-side check uses
// Node's native Ed25519 implementation; the application independently verifies
// with tauri-plugin-updater/minisign-verify before downloading or installing.
export function verifyInstallerSignature(data, signature, publicKey) {
  const publicLines = decode(publicKey).toString('utf8').trim().split(/\r?\n/);
  const lines = decode(signature).toString('utf8').trim().split(/\r?\n/);
  if (publicLines.length !== 2 || lines.length !== 4 || !lines[2].startsWith('trusted comment: ')) throw new Error('Invalid Minisign document');
  const key = decode(publicLines[1]), sig = decode(lines[1]), global = decode(lines[3]);
  if (key.length !== 42 || sig.length !== 74 || global.length !== 64) throw new Error('Invalid Minisign payload');
  if (!['Ed','ED'].includes(key.subarray(0,2).toString()) || !['Ed','ED'].includes(sig.subarray(0,2).toString())) throw new Error('Unsupported signature algorithm');
  if (!key.subarray(2,10).equals(sig.subarray(2,10))) throw new Error('Signature public key does not match');
  const edKey = createPublicKey({key:Buffer.concat([Buffer.from('302a300506032b6570032100','hex'),key.subarray(10)]),format:'der',type:'spki'});
  const message = sig.subarray(0,2).toString() === 'ED' ? createHash('blake2b512').update(data).digest() : data;
  if (!verify(null,message,edKey,sig.subarray(10))) throw new Error('Installer signature is invalid');
  if (!verify(null,Buffer.concat([sig.subarray(10),Buffer.from(lines[2].slice(17))]),edKey,global)) throw new Error('Signature trusted comment is invalid');
  return true;
}
