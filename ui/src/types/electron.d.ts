export {}

declare global {
  interface Window {
    electronAPI?: {
      setIgnoreMouse: (ignore: boolean, forward: boolean) => void
      setStartup: (enabled: boolean) => void
      platform: string
    }
  }
}