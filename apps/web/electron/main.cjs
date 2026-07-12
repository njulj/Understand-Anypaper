const { app, BrowserWindow, dialog, ipcMain } = require('electron');
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
let backendProcess = null;
let quitting = false;

function desktopApiConfigPath() {
  return path.join(app.getPath('userData'), 'desktop-api-config.json');
}

function defaultDesktopApiConfig() {
  return {
    openaiApiKey: process.env.OPENAI_API_KEY || process.env.PAG_OPENAI_API_KEY || '',
    openaiBaseUrl:
      process.env.OPENAI_BASE_URL || process.env.PAG_OPENAI_BASE_URL || DEFAULT_OPENAI_BASE_URL,
    openaiModel: process.env.PAG_OPENAI_MODEL || DEFAULT_OPENAI_MODEL,
  };
}

function normalizeDesktopApiConfig(input = {}) {
  return {
    openaiApiKey: String(input.openaiApiKey || '').trim(),
    openaiBaseUrl: String(input.openaiBaseUrl || '').trim() || DEFAULT_OPENAI_BASE_URL,
    openaiModel: String(input.openaiModel || '').trim() || DEFAULT_OPENAI_MODEL,
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

function backendBaseUrl() {
  return `http://${DESKTOP_BACKEND_HOST}:${DESKTOP_BACKEND_PORT}`;
}

function shouldSpawnPackagedBackend() {
  return app.isPackaged || process.env.PAG_ELECTRON_SPAWN_BACKEND === '1';
}

function backendExecutableName() {
  return process.platform === 'win32' ? 'server.exe' : 'server';
}

function resolveBackendExecutable() {
  if (process.env.PAG_ELECTRON_BACKEND_EXECUTABLE) {
    return process.env.PAG_ELECTRON_BACKEND_EXECUTABLE;
  }
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'backend', backendExecutableName());
  }
  return path.join(__dirname, '..', 'backend', backendExecutableName());
}

function attachBackendLogging(child) {
  child.stdout?.on('data', (chunk) => {
    process.stdout.write(`[python-backend] ${chunk}`);
  });
  child.stderr?.on('data', (chunk) => {
    process.stderr.write(`[python-backend] ${chunk}`);
  });
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

async function startBackendIfNeeded() {
  if (!shouldSpawnPackagedBackend()) {
    process.env.PAG_RENDERER_API_BASE_URL = DEV_API_BASE_URL;
    return;
  }

  const executable = resolveBackendExecutable();
  if (!fs.existsSync(executable)) {
    throw new Error(`Packaged backend executable not found: ${executable}`);
  }

  const documentsDir = path.join(app.getPath('userData'), 'documents');
  fs.mkdirSync(documentsDir, { recursive: true });
  const apiConfig = loadDesktopApiConfig();

  backendProcess = spawn(
    executable,
    [
      '--host',
      DESKTOP_BACKEND_HOST,
      '--port',
      String(DESKTOP_BACKEND_PORT),
      '--document-store-dir',
      documentsDir,
    ],
    {
      env: {
        ...process.env,
        DATABASE_URL: process.env.DATABASE_URL || 'memory',
        PAG_DOCUMENT_STORE_DIR: documentsDir,
        PAG_DESKTOP_SETTINGS_PATH: desktopApiConfigPath(),
        OPENAI_API_KEY: apiConfig.openaiApiKey,
        OPENAI_BASE_URL: apiConfig.openaiBaseUrl,
        PAG_OPENAI_MODEL: apiConfig.openaiModel,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );

  attachBackendLogging(backendProcess);
  backendProcess.once('exit', (code, signal) => {
    if (!quitting) {
      const reason = signal ? `signal ${signal}` : `code ${code}`;
      dialog.showErrorBox('Python backend exited', `The local backend stopped unexpectedly (${reason}).`);
    }
    backendProcess = null;
  });

  process.env.PAG_RENDERER_API_BASE_URL = backendBaseUrl();
  await waitForServer(backendBaseUrl());
}

function stopBackend() {
  if (!backendProcess || backendProcess.killed) {
    return;
  }
  backendProcess.kill();
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
    await createMainWindow();
    await showLoadingScreen(
      mainWindow,
      shouldSpawnPackagedBackend() ? 'Starting the local paper engine...' : 'Opening the workspace...',
    );
    await startBackendIfNeeded();
    await showLoadingScreen(mainWindow, 'Loading the desktop workspace...');
    await loadAppWindow();
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    dialog.showErrorBox('Desktop app failed to start', detail);
    app.quit();
  }
}

app.whenReady().then(bootstrap);

ipcMain.handle('desktop-api-config:get', () => loadDesktopApiConfig());
ipcMain.handle('desktop-api-config:save', (_event, config) => saveDesktopApiConfig(config));

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    void (async () => {
      await createMainWindow();
      await loadAppWindow();
    })();
  }
});

app.on('before-quit', () => {
  quitting = true;
  stopBackend();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
