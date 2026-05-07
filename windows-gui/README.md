# olcRTC Client Windows GUI MVP

Single-exe MVP for Windows. The executable embeds:

- `olcrtc.exe`
- `sing-box.exe`
- PowerShell WinForms GUI

At launch it works in fully portable mode: runtime files, config, logs and `data/` are written next to the GUI `.exe`. It asks for UAC, then starts `olcrtc` and `sing-box` hidden.

## Build

```bash
./windows-gui/build-release.sh v0.1
```

Result:

```text
release/olcrtc-client-gui-v0.1-windows-amd64.exe
release/olcrtc-client-gui-v0.1-windows-amd64.zip
```
