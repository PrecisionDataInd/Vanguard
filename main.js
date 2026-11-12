'use strict';
const { app, BrowserWindow, ipcMain, shell, safeStorage, Menu } = require('electron');
const fs = require('fs');
const path = require('path');
const { createEngine } = require('./src/engine');

let win = null;
let engine = null;

// API keys are encrypted with Windows' own credential protection (DPAPI).
function makeSecretStore(dir) {
  const file = path.join(dir, 'vanguard-keys.bin');
  return {
    load() {
      try {
        if (!fs.existsSync(file) || !safeStorage.isEncryptionAvailable()) return {};
        return JSON.parse(safeStorage.decryptString(fs.readFileSync(file)));
      } catch {
        return {};
      }
    },
    save(obj) {
      if (!safeStorage.isEncryptionAvailable()) throw new Error('Secure storage is unavailable on this computer, so keys were not saved');
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(file, safeStorage.encryptString(JSON.stringify(obj)));
    },
  };
}

function createWindow() {
  win = new BrowserWindow({
    width: 1360,
    height: 880,
    minWidth: 980,
    minHeight: 640,
    backgroundColor: '#0e1512',
    title: 'Vanguard',
    icon: app.isPackaged ? path.join(process.resourcesPath, 'icon.ico') : path.join(__dirname, 'build', 'icon.ico'),
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  // Links never navigate the app window; they open in the default browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (e) => e.preventDefault());
  win.on('closed', () => {
    win = null;
  });
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(() => {
    Menu.setApplicationMenu(null);
    const dataDir = app.getPath('userData');
    const knowledgeDir = app.isPackaged ? path.join(process.resourcesPath, 'knowledge') : path.join(__dirname, 'knowledge');
    engine = createEngine({
      dataDir,
      knowledgeDir,
      secretStore: makeSecretStore(dataDir),
      fetchImpl: (...args) => fetch(...args),
      openExternal: (url) => shell.openExternal(url),
      onChange: () => win && !win.isDestroyed() && win.webContents.send('changed'),
      version: app.getVersion(),
    });

    ipcMain.handle('call', async (_event, method, args) => {
      const fn = Object.prototype.hasOwnProperty.call(engine.api, method) ? engine.api[method] : null;
      if (!fn) return { ok: false, error: `Unknown call: ${method}` };
      try {
        return { ok: true, value: await fn(...(Array.isArray(args) ? args : [])) };
      } catch (err) {
        return { ok: false, error: err.message };
      }
    });
    ipcMain.handle('open-knowledge', () => shell.openPath(knowledgeDir));

    createWindow();
    engine.start();
  });

  app.on('window-all-closed', () => {
    if (engine) engine.stop();
    app.quit();
  });
}
