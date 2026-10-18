FSR4VK INT8 (4.1.1vk and 4.1.1vk Perf)
If you encounter any problems or bugs, please report them in the OptiScaler discord with @de_w23 or put up an issue in GitHub.

Installation steps:
1. Download the OptiScaler Nightly build and install normally. (Found here: https://github.com/optiscaler/OptiScaler-nightly/releases)
2. Extract this .zip directly into the installation, overwriting any files.
3. Install OptiScaler normally using setup_windows.bat or setup_linux.sh.
4. In-game, select FSR 3.X in the Upscaler selector.

Notes:
- If there are any problems, please add into the launch args "FSR4_VK_LOG=1".
- Frame generation is passed through to the stock amd_fidelityfx_vk.dll that
  OptiScaler ships; keep that file next to amd_fidelityfx_upscaler_vk.dll in
  the OptiScaler folder. The provider may create a renamed copy of it
  (fsr4vk-fg-host.dll) because OptiScaler's loader hook serves the original
  name from the provider itself.
