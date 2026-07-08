const { app, BrowserWindow, screen, ipcMain } = require('electron')
const path = require('path')
const { createTray } = require('./tray')

let mainWindow = null
let tray = null
const isDev = !app.isPackaged

app.isQuitting = false

function createWindow() {
  // Only cover the bottom-right corner, not the full screen
  // Position at bottom-right
  const { width, height } = screen.getPrimaryDisplay().workAreaSize

  mainWindow = new BrowserWindow({
    width: width,
    height: height,
    x: 0,
    y: 0,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  // Click-through by default. Orb.tsx toggles this via IPC as the
  // dashboard opens/closes — see the 'set-ignore-mouse' handler below,
  // which was previously missing (this was the root cause of Issue 1).
  mainWindow.setIgnoreMouseEvents(true, { forward: true })
  if (isDev) {
    mainWindow.loadURL('http://localhost:5173')
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }

  if (isDev) {
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  }

  // Issue 4: hide to tray instead of quitting when the window is closed
  mainWindow.on('close', (event) => {
    if (!app.isQuitting) {
      event.preventDefault()
      mainWindow.hide()
    }
  })
}

// ── Issue 1 fix: this handler never existed. preload.js was sending
// 'set-ignore-mouse' over IPC but nothing on the main-process side was
// listening, so setIgnoreMouseEvents() was never actually being called —
// the whole 400x500 window stayed solid-clickable regardless of what
// Orb.tsx thought it was doing. ─────────────────────────────────────────
ipcMain.on('set-ignore-mouse', (event, ignore, forward) => {
  if (mainWindow) {
    mainWindow.setIgnoreMouseEvents(ignore, { forward })
  }
})

// Settings panel → "Start on Boot" toggle (Part A / Issue 3)
ipcMain.on('set-startup', (event, enabled) => {
  app.setLoginItemSettings({ openAtLogin: Boolean(enabled) })
})

app.whenReady().then(() => {
  createWindow()
  tray = createTray(mainWindow, app)
})

app.on('window-all-closed', () => {
  // Do NOT quit here — the tray keeps the app alive. Quitting now only
  // happens via the tray's "Quit" menu item (see tray.js).
  if (process.platform === 'darwin') return
})

app.on('before-quit', () => {
  app.isQuitting = true
})