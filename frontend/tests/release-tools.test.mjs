import test from 'node:test';
import assert from 'node:assert/strict';
import { createManifest, assertVersion, releaseAssetUrl } from '../../scripts/release-tools.mjs';
import { verifyInstallerSignature } from '../../scripts/verify-signature.mjs';
import { generateKeyPairSync, sign, createHash } from 'node:crypto';

test('installer verification rejects tampered data, key and trusted comment', () => {
  const {publicKey,privateKey} = generateKeyPairSync('ed25519');
  const key = publicKey.export({format:'der',type:'spki'}).subarray(-32);
  const keyId = Buffer.from('12345678');
  const publicText = `untrusted comment: public key\n${Buffer.concat([Buffer.from('Ed'),keyId,key]).toString('base64')}\n`;
  const data = Buffer.from('signed installer');
  const signature = sign(null,createHash('blake2b512').update(data).digest(),privateKey);
  const trusted = 'timestamp:1 file:setup.exe';
  const global = sign(null,Buffer.concat([signature,Buffer.from(trusted)]),privateKey);
  const signatureText = `untrusted comment: signature\n${Buffer.concat([Buffer.from('ED'),keyId,signature]).toString('base64')}\ntrusted comment: ${trusted}\n${global.toString('base64')}\n`;
  const pub = Buffer.from(publicText).toString('base64');
  const sig = Buffer.from(signatureText).toString('base64');
  assert.equal(verifyInstallerSignature(data,sig,pub),true);
  assert.throws(()=>verifyInstallerSignature(Buffer.from('tampered'),sig,pub));
  assert.throws(()=>verifyInstallerSignature(data,Buffer.from(signatureText.replace('timestamp:1','timestamp:2')).toString('base64'),pub));
  const wrongKey = Buffer.concat([Buffer.from('Ed'),keyId,Buffer.alloc(32)]);
  assert.throws(()=>verifyInstallerSignature(data,sig,Buffer.from(`untrusted comment: wrong key\n${wrongKey.toString('base64')}\n`).toString('base64')));
});

test('release versions must be stable and valid Windows installer versions', () => {
  assert.equal(assertVersion('0.1.33'), '0.1.33');
  for (const value of ['1.0', 'v1.0.0', '1.2.3-beta', '01.2.3', '256.1.1', '1.256.0', '1.1.65536']) {
    assert.throws(() => assertVersion(value));
  }
});
test('manifest encodes assets and includes the signed Windows installer', () => {
  const manifest = createManifest({version:'0.1.33', notes:'修复更新', installer:'巡检 setup.exe', signature:'signed-data', repository:'ShuaiJustin/network-inspection', date:'2026-10-21T00:00:00Z'});
  assert.equal(manifest.platforms['windows-x86_64'].signature, 'signed-data');
  assert.equal(manifest.platforms['windows-x86_64'].url, releaseAssetUrl('ShuaiJustin/network-inspection', '0.1.33', '巡检 setup.exe'));
  assert.match(manifest.platforms['windows-x86_64'].url, /%20/);
  assert.throws(() => createManifest({version:'0.1.33', installer:'setup.exe', signature:'', repository:'ShuaiJustin/network-inspection'}));
  assert.throws(() => releaseAssetUrl('bad/repo/extra','0.1.33','setup.exe'));
  const both = createManifest({version:'0.1.33',installer:'setup.exe',signature:'exe-sig',msiInstaller:'setup.msi',msiSignature:'msi-sig',repository:'ShuaiJustin/network-inspection'});
  assert.equal(both.platforms['windows-x86_64-msi'].signature,'msi-sig');
  assert.equal(both.platforms['windows-x86_64-nsis'].signature,'exe-sig');
});
