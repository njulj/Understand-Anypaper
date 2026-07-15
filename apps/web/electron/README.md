This directory contains the Electron desktop shell.

`main.cjs` now shows a lightweight startup screen first, then swaps to the
main renderer once the local backend is ready. The app also keeps a tray/menu
bar presence so closing the window hides the workspace while keeping the local
backend warm. In packaged mode, the main process also owns first-run desktop
onboarding: choosing the local workspace location and optionally installing a
reusable `uap` launcher command before the web UI is shown.
