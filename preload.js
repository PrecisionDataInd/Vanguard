'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('vanguard', {
  call: (method, ...args) => ipcRenderer.invoke('call', method, args),
  openKnowledge: () => ipcRenderer.invoke('open-knowledge'),
  onChanged: (fn) => ipcRenderer.on('changed', () => fn()),
});
