const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pagDesktop', {
  apiBaseUrl: process.env.PAG_RENDERER_API_BASE_URL || null,
  isPackaged: process.env.PAG_ELECTRON_IS_PACKAGED === '1',
  platform: process.platform,
  isDesktopApp: true,
  getApiConfig: () => ipcRenderer.invoke('desktop-api-config:get'),
  saveApiConfig: (config) => ipcRenderer.invoke('desktop-api-config:save', config),
  getSetupInfo: () => ipcRenderer.invoke('desktop-setup:get'),
  chooseLatexFolder: () => ipcRenderer.invoke('latex-folder:choose'),
  openVSCodeUrlForFolder: (folderPath) =>
    ipcRenderer.invoke('openvscode:url-for-folder', folderPath),
});
