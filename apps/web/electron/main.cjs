const { app, BrowserWindow, Menu, Tray, dialog, ipcMain, nativeImage } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const DESKTOP_BACKEND_HOST = process.env.PAG_ELECTRON_API_HOST || '127.0.0.1';
const DESKTOP_BACKEND_PORT = Number(process.env.PAG_ELECTRON_API_PORT || '8765');
const DEV_RENDERER_URL = process.env.ELECTRON_RENDERER_URL || 'http://127.0.0.1:5173';
const DEV_API_BASE_URL = process.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';
const LOADING_BACKGROUND = '#f5efe5';
const DEFAULT_OPENAI_BASE_URL = 'https://api.openai.com/v1';
const DEFAULT_OPENAI_MODEL = 'gpt-4o-mini';

let mainWindow = null;
let tray = null;
let quitting = false;

function desktopApiConfigPath() {
  return path.join(app.getPath('userData'), 'desktop-api-config.json');
}

function desktopSetupPath() {
  return path.join(app.getPath('userData'), 'desktop-setup.json');
}

function defaultDesktopApiConfig() {
  return {
    openaiApiKey: process.env.OPENAI_API_KEY || process.env.PAG_OPENAI_API_KEY || '',
    openaiBaseUrl:
      process.env.OPENAI_BASE_URL || process.env.PAG_OPENAI_BASE_URL || DEFAULT_OPENAI_BASE_URL,
    openaiModel: process.env.PAG_OPENAI_MODEL || DEFAULT_OPENAI_MODEL,
  };
}

function defaultDesktopSetup() {
  return {
    workspaceDir: '',
    launcherInstallDir: path.join(app.getPath('userData'), 'bin'),
    launcherCommandPath: '',
    launcherSourcePath: '',
    initializedAt: '',
  };
}

function normalizeDesktopApiConfig(input = {}) {
  return {
    openaiApiKey: String(input.openaiApiKey || '').trim(),
    openaiBaseUrl: String(input.openaiBaseUrl || '').trim() || DEFAULT_OPENAI_BASE_URL,
    openaiModel: String(input.openaiModel || '').trim() || DEFAULT_OPENAI_MODEL,
  };
}

function normalizeDesktopSetup(input = {}) {
  const defaults = defaultDesktopSetup();
  return {
    workspaceDir: String(input.workspaceDir || '').trim(),
    launcherInstallDir: String(input.launcherInstallDir || defaults.launcherInstallDir).trim(),
    launcherCommandPath: String(input.launcherCommandPath || '').trim(),
    launcherSourcePath: String(input.launcherSourcePath || '').trim(),
    initializedAt: String(input.initializedAt || '').trim(),
  };
}

function loadDesktopApiConfig() {
  const filePath = desktopApiConfigPath();
  const defaults = defaultDesktopApiConfig();
  if (!fs.existsSync(filePath)) {
    return defaults;
  }

  try {
    const payload = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    return { ...defaults, ...normalizeDesktopApiConfig(payload) };
  } catch {
    return defaults;
  }
}

function saveDesktopApiConfig(input) {
  const filePath = desktopApiConfigPath();
  const nextConfig = normalizeDesktopApiConfig(input);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(nextConfig, null, 2)}\n`, 'utf8');
  return nextConfig;
}

function loadDesktopSetup() {
  const filePath = desktopSetupPath();
  const defaults = defaultDesktopSetup();
  if (!fs.existsSync(filePath)) {
    return defaults;
  }

  try {
    const payload = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    return { ...defaults, ...normalizeDesktopSetup(payload) };
  } catch {
    return defaults;
  }
}

function saveDesktopSetup(input) {
  const filePath = desktopSetupPath();
  const nextSetup = normalizeDesktopSetup(input);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(nextSetup, null, 2)}\n`, 'utf8');
  return nextSetup;
}

function backendBaseUrl() {
  return `http://${DESKTOP_BACKEND_HOST}:${DESKTOP_BACKEND_PORT}`;
}

function shouldSpawnPackagedBackend() {
  return app.isPackaged || process.env.PAG_ELECTRON_SPAWN_BACKEND === '1';
}

function launcherExecutableName() {
  return process.platform === 'win32' ? 'uap.exe' : 'uap';
}

function resolveLauncherExecutable() {
  if (process.env.PAG_ELECTRON_LAUNCHER_EXECUTABLE) {
    return process.env.PAG_ELECTRON_LAUNCHER_EXECUTABLE;
  }
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'launcher', launcherExecutableName());
  }
  return path.join(__dirname, '..', '..', 'cli');
}

function resolveCLIInvocation(args) {
  if (app.isPackaged) {
    const launcher = resolveLauncherExecutable();
    if (!fs.existsSync(launcher)) {
      throw new Error(`Desktop launcher executable not found: ${launcher}`);
    }
    return {
      command: launcher,
      args,
      cwd: path.dirname(launcher),
    };
  }

  return {
    command: 'go',
    args: ['run', './cmd', ...args],
    cwd: resolveLauncherExecutable(),
  };
}

function runCLICommand(args) {
  const invocation = resolveCLIInvocation(args);
  return new Promise((resolve, reject) => {
    const child = spawn(invocation.command, invocation.args, {
      cwd: invocation.cwd,
      env: {
        ...process.env,
        PAG_DESKTOP_SETTINGS_PATH: desktopApiConfigPath(),
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    child.stdout?.on('data', (chunk) => {
      const text = String(chunk);
      stdout += text;
      process.stdout.write(`[uap] ${text}`);
    });
    child.stderr?.on('data', (chunk) => {
      const text = String(chunk);
      stderr += text;
      process.stderr.write(`[uap] ${text}`);
    });
    child.once('error', reject);
    child.once('exit', (code) => {
      if (code === 0) {
        resolve({ stdout, stderr });
        return;
      }
      reject(new Error(stderr.trim() || stdout.trim() || `Command failed with exit code ${code}`));
    });
  });
}

async function runCLIJSON(args) {
  const { stdout } = await runCLICommand(['--json', ...args]);
  try {
    return JSON.parse(stdout);
  } catch (error) {
    throw new Error(`Failed to parse JSON output for ${args.join(' ')}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function launcherCommandName() {
  return process.platform === 'win32' ? 'uap.cmd' : 'uap';
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\"'\"'`)}'`;
}

function installLauncherCommand(installDir, setup) {
  const sourceLauncher = resolveLauncherExecutable();
  if (!fs.existsSync(sourceLauncher)) {
    throw new Error(`Launcher executable not found: ${sourceLauncher}`);
  }

  fs.mkdirSync(installDir, { recursive: true });
  const commandPath = path.join(installDir, launcherCommandName());
  const settingsPath = desktopApiConfigPath();
  const workspaceDir = setup?.workspaceDir || '';
  if (process.platform === 'win32') {
    const sourcePath = sourceLauncher.replace(/\//g, '\\');
    const script = `@echo off\r\nset "PAG_DESKTOP_SETTINGS_PATH=${settingsPath}"\r\nset "PAG_WORKSPACE_DIR=${workspaceDir}"\r\n"${sourcePath}" %*\r\n`;
    fs.writeFileSync(commandPath, script, 'utf8');
  } else {
    const exports = [
      `export PAG_DESKTOP_SETTINGS_PATH=${shellQuote(settingsPath)}`,
      `export PAG_WORKSPACE_DIR=${shellQuote(workspaceDir)}`,
    ].join('\n');
    const script = `#!/usr/bin/env bash\n${exports}\nexec ${shellQuote(sourceLauncher)} "$@"\n`;
    fs.writeFileSync(commandPath, script, 'utf8');
    fs.chmodSync(commandPath, 0o755);
  }

  return {
    launcherInstallDir: installDir,
    launcherCommandPath: commandPath,
    launcherSourcePath: sourceLauncher,
  };
}

function workspaceArgsForSetup(setup) {
  return setup?.workspaceDir ? ['--workspace', setup.workspaceDir] : [];
}

async function promptForWorkspaceSetup() {
  const response = await dialog.showMessageBox({
    type: 'question',
    buttons: ['Use Recommended Location', 'Choose Location', 'Quit'],
    defaultId: 0,
    cancelId: 2,
    title: 'Set Up Understand Anypaper',
    message: 'Choose where the desktop app should store its local workspace.',
    detail:
      'The workspace contains your SQLite database, uploaded papers, logs, and cache. You can keep the recommended location or pick a custom folder.',
    noLink: true,
  });

  if (response.response === 2) {
    throw new Error('Desktop setup was canceled before a workspace was selected.');
  }

  if (response.response === 0) {
    const result = await runCLIJSON(['init']);
    return result.workspace.root;
  }

  const selection = await dialog.showOpenDialog({
    title: 'Choose Workspace Folder',
    buttonLabel: 'Use This Folder',
    properties: ['openDirectory', 'createDirectory'],
  });
  if (selection.canceled || !selection.filePaths[0]) {
    throw new Error('Desktop setup was canceled before a workspace folder was chosen.');
  }

  const result = await runCLIJSON(['init', '--path', selection.filePaths[0]]);
  return result.workspace.root;
}

async function maybeInstallLauncherCommand(currentSetup) {
  const installChoice = await dialog.showMessageBox({
    type: 'question',
    buttons: ['Install uap Command', 'Skip'],
    defaultId: 0,
    cancelId: 1,
    title: 'Optional CLI Installation',
    message: 'Do you also want a reusable `uap` command?',
    detail:
      'Understand Anypaper can place a small launcher command in a folder you choose, so you can call `uap` from Terminal or PowerShell after adding that folder to PATH.',
    noLink: true,
  });
  if (installChoice.response !== 0) {
    return currentSetup;
  }

  const locationChoice = await dialog.showMessageBox({
    type: 'question',
    buttons: ['Use Recommended Folder', 'Choose Folder', 'Skip'],
    defaultId: 0,
    cancelId: 2,
    title: 'Choose uap Install Folder',
    message: 'Choose where to place the `uap` launcher command.',
    detail: `Recommended: ${currentSetup.launcherInstallDir}`,
    noLink: true,
  });
  if (locationChoice.response === 2) {
    return currentSetup;
  }

  let installDir = currentSetup.launcherInstallDir;
  if (locationChoice.response === 1) {
    const selection = await dialog.showOpenDialog({
      title: 'Choose Folder for uap',
      buttonLabel: 'Install Here',
      properties: ['openDirectory', 'createDirectory'],
      defaultPath: currentSetup.launcherInstallDir,
    });
    if (selection.canceled || !selection.filePaths[0]) {
      return currentSetup;
    }
    installDir = selection.filePaths[0];
  }

  const installResult = installLauncherCommand(installDir, currentSetup);
  const nextSetup = saveDesktopSetup({
    ...currentSetup,
    ...installResult,
  });
  await dialog.showMessageBox({
    type: 'info',
    buttons: ['OK'],
    defaultId: 0,
    title: 'uap Installed',
    message: 'The desktop launcher command is ready.',
    detail: `Installed at:\n${nextSetup.launcherCommandPath}\n\nIf you want to run it from a shell, add this folder to PATH:\n${nextSetup.launcherInstallDir}`,
    noLink: true,
  });
  return nextSetup;
}

async function ensureDesktopSetup() {
  if (!shouldSpawnPackagedBackend()) {
    return loadDesktopSetup();
  }

  let setup = loadDesktopSetup();
  if (setup.workspaceDir && fs.existsSync(setup.workspaceDir)) {
    return setup;
  }

  const workspaceDir = await promptForWorkspaceSetup();
  setup = saveDesktopSetup({
    ...setup,
    workspaceDir,
    initializedAt: new Date().toISOString(),
  });
  setup = await maybeInstallLauncherCommand(setup);
  return setup;
}

function loadingMarkup(message) {
  return `<!doctype html>
  <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>Understand Anypaper</title>
      <style>
        :root {
          color-scheme: light;
          --bg: #f5efe5;
          --surface: rgba(255, 255, 255, 0.78);
          --text: #1a1b1e;
          --muted: #6a6f7a;
          --accent: #146356;
          --accent-soft: rgba(20, 99, 86, 0.14);
        }

        * { box-sizing: border-box; }

        body {
          margin: 0;
          min-height: 100vh;
          display: grid;
          place-items: center;
          overflow: hidden;
          background:
            radial-gradient(circle at top left, rgba(20, 99, 86, 0.18), transparent 38%),
            radial-gradient(circle at bottom right, rgba(193, 117, 59, 0.18), transparent 34%),
            linear-gradient(135deg, #fbf7f0 0%, var(--bg) 50%, #efe7db 100%);
          font-family: "Avenir Next", "Segoe UI", sans-serif;
          color: var(--text);
        }

        .shell {
          position: relative;
          width: min(560px, calc(100vw - 48px));
          padding: 36px 34px 30px;
          border-radius: 28px;
          background: var(--surface);
          border: 1px solid rgba(255, 255, 255, 0.85);
          box-shadow: 0 28px 90px rgba(62, 45, 29, 0.12);
          backdrop-filter: blur(18px);
        }

        .badge {
          display: inline-flex;
          align-items: center;
          gap: 10px;
          padding: 8px 12px;
          border-radius: 999px;
          background: rgba(255, 255, 255, 0.72);
          color: var(--muted);
          font-size: 12px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
        }

        .dot {
          width: 9px;
          height: 9px;
          border-radius: 50%;
          background: var(--accent);
          box-shadow: 0 0 0 0 var(--accent-soft);
          animation: pulse 1.8s infinite;
        }

        h1 {
          margin: 22px 0 10px;
          font-size: clamp(30px, 5vw, 46px);
          line-height: 1.02;
          letter-spacing: -0.04em;
        }

        p {
          margin: 0;
          color: var(--muted);
          font-size: 15px;
          line-height: 1.6;
        }

        .status {
          margin-top: 28px;
          display: flex;
          align-items: center;
          gap: 16px;
          padding: 16px 18px;
          border-radius: 20px;
          background: rgba(255, 255, 255, 0.8);
          border: 1px solid rgba(20, 99, 86, 0.08);
        }

        .ring {
          width: 46px;
          height: 46px;
          border-radius: 50%;
          border: 4px solid rgba(20, 99, 86, 0.12);
          border-top-color: var(--accent);
          animation: spin 1s linear infinite;
          flex: 0 0 auto;
        }

        .status strong {
          display: block;
          font-size: 15px;
          margin-bottom: 2px;
        }

        .status span {
          color: var(--muted);
          font-size: 13px;
        }

        .grid {
          position: absolute;
          inset: auto -70px -60px auto;
          width: 240px;
          height: 240px;
          opacity: 0.24;
          background-image:
            linear-gradient(rgba(20, 99, 86, 0.3) 1px, transparent 1px),
            linear-gradient(90deg, rgba(20, 99, 86, 0.3) 1px, transparent 1px);
          background-size: 18px 18px;
          transform: rotate(-12deg);
          pointer-events: none;
        }

        @keyframes spin {
          to { transform: rotate(360deg); }
        }

        @keyframes pulse {
          0% { box-shadow: 0 0 0 0 rgba(20, 99, 86, 0.22); }
          70% { box-shadow: 0 0 0 16px rgba(20, 99, 86, 0); }
          100% { box-shadow: 0 0 0 0 rgba(20, 99, 86, 0); }
        }
      </style>
    </head>
    <body>
      <section class="shell">
        <div class="badge"><span class="dot"></span> Desktop Startup</div>
        <h1>Understand<br />Anypaper</h1>
        <p>Preparing the local analysis engine and restoring your workspace.</p>
        <div class="status">
          <div class="ring" aria-hidden="true"></div>
          <div>
            <strong>${message}</strong>
            <span>This usually takes a few seconds.</span>
          </div>
        </div>
        <div class="grid" aria-hidden="true"></div>
      </section>
    </body>
  </html>`;
}

function createTrayImage() {
  const svg =
    process.platform === 'darwin'
      ? `
        <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22">
          <path d="M6.2 16.8 L11 5.4 L15.8 16.8" fill="none" stroke="#000000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
          <path d="M7.9 12.6 H14.1" fill="none" stroke="#000000" stroke-width="2" stroke-linecap="round" />
        </svg>
      `.trim()
      : `
        <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
          <rect x="4" y="4" width="24" height="24" rx="8" fill="#146356" />
          <path d="M10 21 L16 10 L22 21" fill="none" stroke="#ffffff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
          <path d="M12.7 17.2 H19.3" fill="none" stroke="#ffffff" stroke-width="2.4" stroke-linecap="round" />
        </svg>
      `.trim();
  const image = nativeImage.createFromDataURL(
    `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
  );
  const resized = image.resize(
    process.platform === 'darwin' ? { width: 16, height: 16 } : { width: 18, height: 18 },
  );
  if (process.platform === 'darwin') {
    resized.setTemplateImage(true);
  }
  return resized;
}

function showMainWindow() {
  if (!mainWindow) {
    return;
  }
  if (process.platform === 'darwin') {
    app.dock.show();
  }
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  mainWindow.focus();
  updateTrayMenu();
}

function hideMainWindow() {
  if (!mainWindow) {
    return;
  }
  mainWindow.hide();
  if (process.platform === 'darwin') {
    app.dock.hide();
  }
  updateTrayMenu();
}

function toggleMainWindow() {
  if (!mainWindow) {
    return;
  }
  if (mainWindow.isVisible()) {
    hideMainWindow();
    return;
  }
  showMainWindow();
}

function updateTrayMenu() {
  if (!tray) {
    return;
  }
  const isVisible = Boolean(mainWindow?.isVisible());
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: 'Start Service',
        click: () => {
          void ensureLocalService(true);
        },
      },
      {
        label: 'Stop Service',
        click: () => {
          void stopLocalService(true);
        },
      },
      {
        label: isVisible ? 'Hide Workspace' : 'Open Workspace',
        click: () => toggleMainWindow(),
      },
      {
        label: 'Quit',
        click: () => {
          quitting = true;
          app.quit();
        },
      },
    ]),
  );
}

function createTray() {
  if (tray) {
    return;
  }
  tray = new Tray(createTrayImage());
  tray.setToolTip('Understand Anypaper');
  tray.on('click', () => toggleMainWindow());
  updateTrayMenu();
}

async function showLoadingScreen(window, message) {
  await window.loadURL(`data:text/html;charset=UTF-8,${encodeURIComponent(loadingMarkup(message))}`);
}

async function waitForServer(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;

  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${url}/health`);
      if (response.ok) {
        return;
      }
      lastError = new Error(`Health check failed with HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  throw lastError || new Error(`Timed out waiting for backend at ${url}`);
}

async function startBackendIfNeeded(setup) {
  if (!shouldSpawnPackagedBackend()) {
    process.env.PAG_RENDERER_API_BASE_URL = DEV_API_BASE_URL;
    return;
  }
  await ensureLocalService(false, setup);
}

async function ensureLocalService(showErrors, setup = loadDesktopSetup()) {
  try {
    await runCLICommand(['service', 'start', ...workspaceArgsForSetup(setup)]);
    process.env.PAG_RENDERER_API_BASE_URL = backendBaseUrl();
    await waitForServer(backendBaseUrl());
  } catch (error) {
    if (showErrors) {
      const detail = error instanceof Error ? error.message : String(error);
      dialog.showErrorBox('Failed to start local service', detail);
    }
    throw error;
  }
}

async function stopLocalService(showErrors) {
  try {
    await runCLICommand(['service', 'stop']);
  } catch (error) {
    if (showErrors) {
      const detail = error instanceof Error ? error.message : String(error);
      dialog.showErrorBox('Failed to stop local service', detail);
    }
    throw error;
  }
}

async function createMainWindow() {
  process.env.PAG_ELECTRON_IS_PACKAGED = app.isPackaged ? '1' : '0';

  mainWindow = new BrowserWindow({
    width: 1600,
    height: 980,
    minWidth: 1200,
    minHeight: 760,
    autoHideMenuBar: true,
    show: false,
    backgroundColor: LOADING_BACKGROUND,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
  });
  mainWindow.on('show', () => {
    if (process.platform === 'darwin') {
      app.dock.show();
    }
    updateTrayMenu();
  });
  mainWindow.on('hide', () => {
    if (process.platform === 'darwin') {
      app.dock.hide();
    }
    updateTrayMenu();
  });
  mainWindow.on('close', (event) => {
    if (quitting) {
      return;
    }
    event.preventDefault();
    hideMainWindow();
  });
  mainWindow.on('closed', () => {
    mainWindow = null;
    updateTrayMenu();
  });
}

async function loadAppWindow() {
  if (!mainWindow) {
    return;
  }

  if (app.isPackaged) {
    await mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
    return;
  }

  await mainWindow.loadURL(DEV_RENDERER_URL);
  mainWindow.webContents.openDevTools({ mode: 'detach' });
}

async function bootstrap() {
  try {
    const desktopSetup = await ensureDesktopSetup();
    await createMainWindow();
    createTray();
    await showLoadingScreen(
      mainWindow,
      shouldSpawnPackagedBackend() ? 'Starting the local paper engine...' : 'Opening the workspace...',
    );
    await startBackendIfNeeded(desktopSetup);
    await showLoadingScreen(mainWindow, 'Loading the desktop workspace...');
    await loadAppWindow();
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    dialog.showErrorBox('Desktop app failed to start', detail);
    app.quit();
  }
}

const gotSingleInstanceLock = app.requestSingleInstanceLock();

if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    showMainWindow();
  });
  app.whenReady().then(bootstrap);
}

ipcMain.handle('desktop-api-config:get', () => loadDesktopApiConfig());
ipcMain.handle('desktop-api-config:save', (_event, config) => saveDesktopApiConfig(config));
ipcMain.handle('desktop-setup:get', () => loadDesktopSetup());

app.on('activate', () => {
  if (mainWindow) {
    showMainWindow();
    return;
  }
  void bootstrap();
});

app.on('before-quit', () => {
  quitting = true;
});

app.on('window-all-closed', () => {
  // Keep the process resident in the tray so the backend remains warm.
});
