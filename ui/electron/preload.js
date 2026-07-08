const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  setIgnoreMouse: (ignore, forward) => {
    ipcRenderer.send('set-ignore-mouse', ignore, forward)
  },
  setStartup: (enabled) => {
    ipcRenderer.send('set-startup', enabled)
  },
  platform: process.platform,
})