# olcRTC Easy Windows GUI MVP

Single-exe MVP for Windows. The executable embeds:

- `olcrtc.exe`
- `sing-box.exe`
- PowerShell WinForms GUI

At launch it extracts runtime files to `%LOCALAPPDATA%\\olcrtc-easy\\windows-gui`, asks for UAC on connect, then starts `olcrtc` and `sing-box` hidden.

## Build

```bash
./windows-gui/build-release.sh v0.1
```

Result:

```text
release/olcrtc-easy-gui-v0.1-windows-amd64.exe
release/olcrtc-easy-gui-v0.1-windows-amd64.zip
```
