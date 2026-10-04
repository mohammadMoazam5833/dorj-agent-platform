'use strict'
// Only the local offline/settings page uses this bridge; remote pages get nothing extra.
const { contextBridge, ipcRenderer } = require('electron')

if (location.protocol === 'file:') {
  contextBridge.exposeInMainWorld('nova', {
    getServer: () => ipcRenderer.invoke('nova:get-server'),
    setServer: (url) => ipcRenderer.invoke('nova:set-server', url),
    retry: () => ipcRenderer.invoke('nova:retry')
  })
}
