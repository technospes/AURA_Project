const { Tray, Menu, nativeImage } = require('electron')

// A small 32x32 blue circle, generated once and embedded as base64 so no
// external icon asset file is required. Matches the orb's glass-blue palette.
const ICON_DATA_URL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAyUlEQVR4nO2XwQ2AMAhF1bMDuIHruItzuIvruIEDeNcTiSIgVbAk+q8t/JfPoaUovq7yTtG8rCt31tRlUk/1Zcn0CYwKAJv348TeHbo2CUI83BtLphoYDqTyMsd13AhJAAtzLQSbgIW5ps8JACitzDEETuEA4GUuQYgjeENxALzjB+ExxEngB/gBsgPAc4nfc2tBf/CLk0AIAO8x4PhPAJ4QlDkJQBVZmVMiAfaUTyGuPqZsAhYQml9x7L1AgtDIbDNKgUndDbNrA7L3cTR8AyiJAAAAAElFTkSuQmCC'

let tray = null

function createTray(mainWindow, app) {
  const icon = nativeImage.createFromDataURL(ICON_DATA_URL)
  tray = new Tray(icon.resize({ width: 16, height: 16 }))

  const rebuildMenu = () => {
    const visible = mainWindow.isVisible()
    const menu = Menu.buildFromTemplate([
      {
        label: visible ? 'Hide Orb' : 'Show Orb',
        click: () => {
          if (mainWindow.isVisible()) {
            mainWindow.hide()
          } else {
            mainWindow.show()
          }
          rebuildMenu()
        },
      },
      { type: 'separator' },
      {
        label: 'Quit',
        click: () => {
          app.isQuitting = true
          app.quit()
        },
      },
    ])
    tray.setContextMenu(menu)
  }

  rebuildMenu()
  tray.setToolTip('Jarvis')

  // Left-click toggles visibility directly (common tray UX);
  // right-click still shows the full context menu via setContextMenu above.
  tray.on('click', () => {
    mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show()
    rebuildMenu()
  })

  return tray
}

module.exports = { createTray }