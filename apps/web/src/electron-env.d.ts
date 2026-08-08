export {};

declare global {
  interface DesktopApiConfig {
    openaiApiKey: string;
    openaiBaseUrl: string;
    openaiModel: string;
    sendPromptCacheKey: boolean;
  }

  interface DesktopSetupInfo {
    workspaceDir: string;
    launcherInstallDir: string;
    launcherCommandPath: string;
    launcherSourcePath: string;
    initializedAt: string;
  }

  interface Window {
    pagDesktop?: {
      apiBaseUrl?: string | null;
      isPackaged: boolean;
      platform: string;
      isDesktopApp: boolean;
      getApiConfig: () => Promise<DesktopApiConfig>;
      saveApiConfig: (config: DesktopApiConfig) => Promise<DesktopApiConfig>;
      getSetupInfo: () => Promise<DesktopSetupInfo>;
      openGraphWindow: (options: {
        paperId: string;
        nodeId?: string | null;
        mock?: boolean;
      }) => Promise<number>;
      chooseLatexFolder: () => Promise<string | null>;
      openVSCodeUrlForFolder: (folderPath: string) => Promise<string>;
    };
  }
}
