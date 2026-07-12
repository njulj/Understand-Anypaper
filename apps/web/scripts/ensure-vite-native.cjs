const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const PLATFORM_BINDINGS = {
  darwin: {
    arm64: '@rolldown/binding-darwin-arm64',
    x64: '@rolldown/binding-darwin-x64',
  },
  linux: {
    arm64: '@rolldown/binding-linux-arm64-gnu',
    x64: '@rolldown/binding-linux-x64-gnu',
  },
  win32: {
    arm64: '@rolldown/binding-win32-arm64-msvc',
    x64: '@rolldown/binding-win32-x64-msvc',
  },
};

function bindingPackageForCurrentPlatform() {
  return PLATFORM_BINDINGS[process.platform]?.[process.arch] ?? null;
}

function localPackageRoot(packageName) {
  return path.join(__dirname, '..', 'node_modules', ...packageName.split('/'));
}

function bindingEntryPoint(packageName) {
  const packageJsonPath = path.join(localPackageRoot(packageName), 'package.json');
  if (!fs.existsSync(packageJsonPath)) {
    return null;
  }
  const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'));
  if (!packageJson.main) {
    return null;
  }
  return path.join(path.dirname(packageJsonPath), packageJson.main);
}

function validateBinding(packageName) {
  const entryPoint = bindingEntryPoint(packageName);
  if (!entryPoint || !fs.existsSync(entryPoint)) {
    return { ok: false, reason: `Missing native entry for ${packageName}` };
  }

  try {
    require(entryPoint);
    return { ok: true };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { ok: false, reason: message };
  }
}

function installBinding(packageName) {
  const result = spawnSync(
    'npm',
    ['install', '--no-save', '--force', packageName],
    {
      cwd: path.join(__dirname, '..'),
      stdio: 'inherit',
      env: process.env,
    },
  );

  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function main() {
  const packageName = bindingPackageForCurrentPlatform();
  if (!packageName) {
    console.warn(
      `[doctor:bindings] No rolldown native binding mapping for ${process.platform}/${process.arch}; skipping verification.`,
    );
    return;
  }

  const firstCheck = validateBinding(packageName);
  if (firstCheck.ok) {
    return;
  }

  console.warn(`[doctor:bindings] ${firstCheck.reason}`);
  console.warn(`[doctor:bindings] Reinstalling ${packageName} before Vite build...`);
  installBinding(packageName);

  const secondCheck = validateBinding(packageName);
  if (!secondCheck.ok) {
    console.error(`[doctor:bindings] Failed to repair ${packageName}: ${secondCheck.reason}`);
    process.exit(1);
  }
}

main();
