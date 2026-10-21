import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { verifyInstallerSignature } from './verify-signature.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export function assertVersion(value) {
  if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value)) throw new Error('Use a stable version: MAJOR.MINOR.PATCH');
  const [major, minor, patch] = value.split('.').map(Number);
  if (major > 255 || minor > 255 || patch > 65535) throw new Error('Version exceeds Windows MSI limits');
  return value;
}
export function releaseAssetUrl(repository, version, name) {
  assertVersion(version);
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)) throw new Error('Invalid GitHub repository');
  if (path.basename(name) !== name || /[\\/]/.test(name)) throw new Error('Expected an asset filename');
  return `https://github.com/${repository}/releases/download/v${version}/${encodeURIComponent(name)}`;
}
export function createManifest({ version, notes = '', installer, signature, msiInstaller, msiSignature, repository, date = new Date().toISOString() }) {
  assertVersion(version);
  if (!signature?.trim()) throw new Error('A signed installer is required');
  if (Number.isNaN(Date.parse(date))) throw new Error('Invalid release date');
  const nsis = { signature: signature.trim(), url: releaseAssetUrl(repository, version, installer) };
  const platforms = { 'windows-x86_64': nsis, 'windows-x86_64-nsis': nsis };
  if (msiInstaller || msiSignature) {
    if (!msiInstaller || !msiSignature?.trim()) throw new Error('A signed MSI installer is required');
    platforms['windows-x86_64-msi'] = { signature:msiSignature.trim(),url:releaseAssetUrl(repository,version,msiInstaller) };
  }
  return { version, notes, pub_date: date, platforms };
}
function readVersions() {
  const config = JSON.parse(fs.readFileSync(path.join(root, 'src-tauri/tauri.conf.json'), 'utf8'));
  const cargo = fs.readFileSync(path.join(root, 'src-tauri/Cargo.toml'), 'utf8').match(/^version = "([^"]+)"/m)?.[1];
  const locked = fs.readFileSync(path.join(root, 'src-tauri/Cargo.lock'), 'utf8').match(/name = "network-inspection"\r?\nversion = "([^"]+)"/)?.[1];
  const frontend = JSON.parse(fs.readFileSync(path.join(root, 'frontend/package.json'), 'utf8'));
  const lock = JSON.parse(fs.readFileSync(path.join(root, 'frontend/package-lock.json'), 'utf8'));
  const backend = fs.readFileSync(path.join(root, 'backend/app/version.py'), 'utf8').match(/__version__ = "([^"]+)"/)?.[1];
  return { tauri:config.version, cargo, locked, frontend:frontend.version, npmLock:lock.version, npmRoot:lock.packages[''].version, backend };
}
function check() {
  const versions = readVersions();
  const version = assertVersion(versions.tauri);
  for (const [source, value] of Object.entries(versions)) if (value !== version) throw new Error(`${source} version ${value} differs from ${version}`);
  console.log(`All application versions: ${version}`);
  return version;
}
function setVersion(version) {
  assertVersion(version);
  for (const name of ['src-tauri/tauri.conf.json', 'frontend/package.json', 'frontend/package-lock.json']) {
    const p = path.join(root, name), data = JSON.parse(fs.readFileSync(p,'utf8'));
    data.version = version;
    if (data.packages?.['']) data.packages[''].version = version;
    fs.writeFileSync(p, JSON.stringify(data,null,2)+'\n');
  }
  for (const name of ['src-tauri/Cargo.toml','src-tauri/Cargo.lock']) {
    const p = path.join(root,name), source = fs.readFileSync(p,'utf8');
    fs.writeFileSync(p, name.endsWith('.toml') ? source.replace(/^version = "[^"]+"/m,`version = "${version}"`) : source.replace(/(name = "network-inspection"\r?\n)version = "[^"]+"/,`$1version = "${version}"`));
  }
  fs.writeFileSync(path.join(root,'backend/app/version.py'),`# Kept in sync by scripts/release-tools.mjs\n__version__ = "${version}"\n`);
  check();
}
function prepare(directory, notesFile, repository) {
  const version = check(), bundle = path.resolve(directory);
  const installers = fs.readdirSync(bundle).filter(n=>n.endsWith('-setup.exe'));
  if (installers.length !== 1) throw new Error('Release directory must contain exactly one NSIS installer');
  const installer = installers[0];
  if (!installer.includes(`_${version}_`)) throw new Error('Installer filename version differs from project');
  const assets = fs.readdirSync(bundle).filter(n=>/\.(exe|msi)$/.test(n));
  const publicKey = JSON.parse(fs.readFileSync(path.join(root,'src-tauri/tauri.conf.json'),'utf8')).plugins.updater.pubkey;
  if (!assets.some(n=>n.endsWith('.msi'))) throw new Error('MSI installer missing');
  for (const name of assets) {
    if (!name.includes(`_${version}_`)) throw new Error(`Unexpected version: ${name}`);
    if (!fs.readFileSync(path.join(bundle,name+'.sig'),'utf8').trim()) throw new Error(`Missing signature: ${name}`);
    verifyInstallerSignature(fs.readFileSync(path.join(bundle,name)),fs.readFileSync(path.join(bundle,name+'.sig'),'utf8'),publicKey);
  }
  const msiInstaller = assets.find(n=>n.endsWith('.msi'));
  const manifest = createManifest({version,notes:fs.readFileSync(notesFile,'utf8'),installer,signature:fs.readFileSync(path.join(bundle,installer+'.sig'),'utf8'),msiInstaller,msiSignature:fs.readFileSync(path.join(bundle,msiInstaller+'.sig'),'utf8'),repository});
  fs.writeFileSync(path.join(bundle,'latest.json'),JSON.stringify(manifest,null,2)+'\n');
  const checksums = [...assets,...assets.map(n=>n+'.sig'),'latest.json'].sort().map(name=>`${createHash('sha256').update(fs.readFileSync(path.join(bundle,name))).digest('hex')}  ${name}`).join('\n')+'\n';
  fs.writeFileSync(path.join(bundle,'SHA256SUMS.txt'),checksums);
  console.log(`Prepared signed release v${version} in ${bundle}`);
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [command,...args] = process.argv.slice(2);
    if (command === 'check') check();
    else if (command === 'set') setVersion(args[0]);
    else if (command === 'prepare') prepare(args[0],args[1],args[2] || 'ShuaiJustin/network-inspection');
    else throw new Error('Usage: node scripts/release-tools.mjs check | set VERSION | prepare DIRECTORY NOTES_FILE [OWNER/REPO]');
  } catch (error) { console.error(error.message); process.exitCode=1; }
}
