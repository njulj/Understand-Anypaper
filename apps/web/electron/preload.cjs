const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pagDesktop', {
  apiBaseUrl: process.env.PAG_RENDERER_API_BASE_URL || null,
  isPackaged: process.env.PAG_ELECTRON_IS_PACKAGED === '1',
  platform: process.platform,
  isDesktopApp: true,
  getApiConfig: () => ipcRenderer.invoke('desktop-api-config:get'),
  saveApiConfig: (config) => ipcRenderer.invoke('desktop-api-config:save', config),
  getSetupInfo: () => ipcRenderer.invoke('desktop-setup:get'),
  getGraphWindowState: () => ipcRenderer.invoke('window:graph-state:get'),
  openGraphWindow: (options) => ipcRenderer.invoke('window:open-graph', options),
  updateGraphWindow: (options) => ipcRenderer.invoke('window:update-graph', options),
  publishGraphSelection: (selection) => ipcRenderer.send('window:graph-selection', selection),
  onGraphWindowStateChange: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('window:graph-state', listener);
    return () => ipcRenderer.removeListener('window:graph-state', listener);
  },
  onGraphWindowNavigate: (callback) => {
    const listener = (_event, options) => callback(options);
    ipcRenderer.on('window:graph-navigate', listener);
    return () => ipcRenderer.removeListener('window:graph-navigate', listener);
  },
  onGraphSelection: (callback) => {
    const listener = (_event, selection) => callback(selection);
    ipcRenderer.on('window:graph-selection', listener);
    return () => ipcRenderer.removeListener('window:graph-selection', listener);
  },
  chooseLatexFolder: () => ipcRenderer.invoke('latex-folder:choose'),
  openVSCodeUrlForFolder: (folderPath) =>
    ipcRenderer.invoke('openvscode:url-for-folder', folderPath),
});
