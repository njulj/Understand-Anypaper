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
      getGraphWindowState: () => Promise<{ open: boolean }>;
      openGraphWindow: (options: {
        paperId: string;
        nodeId?: string | null;
        focusRevision?: number;
        mock?: boolean;
      }) => Promise<number>;
      updateGraphWindow: (options: {
        paperId: string;
        nodeId?: string | null;
        focusRevision?: number;
        mock?: boolean;
      }) => Promise<boolean>;
      publishGraphSelection: (selection: {
        paperId: string;
        nodeId?: string | null;
      }) => void;
      onGraphWindowStateChange: (
        callback: (state: { open: boolean }) => void,
      ) => () => void;
      onGraphWindowNavigate: (
        callback: (options: {
          paperId: string;
          nodeId?: string | null;
          focusRevision?: number;
          mock?: boolean;
        }) => void,
      ) => () => void;
      onGraphSelection: (
        callback: (selection: { paperId: string; nodeId?: string | null }) => void,
      ) => () => void;
      chooseLatexFolder: () => Promise<string | null>;
      openVSCodeUrlForFolder: (folderPath: string) => Promise<string>;
    };
  }
}
