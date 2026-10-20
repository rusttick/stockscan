# stockscan

Photo inventory of machine shop metal stock.

The goal: instead of measuring every bar and plate on the shelves by hand,
take photos of the shelves and let the software measure each piece. See
[doc/problem.md](doc/problem.md), [doc/research.md](doc/research.md) and
[doc/design1.md](doc/design1.md).

**Version 0.1.0** is the first field release. It does not keep an inventory
yet. It answers the first practical questions: does it install, can the
cameras be calibrated, what target size works, and how accurate the
end-face (width × height) geometry is. It is a small web app that runs on a
desktop computer and is used from a browser.

## Requirements

- Windows 10/11, macOS or Linux, 64-bit.
- Python 3.11, 3.12 or 3.13.
- About 500 MB of disk for the installed packages, plus room for photos.

Nothing is compiled during installation; all dependencies come as prebuilt
packages from PyPI.

## Install on Windows

1. **Install Python** 3.13 (or 3.11/3.12) from
   [python.org/downloads](https://www.python.org/downloads/windows/).
   In the installer, keep "Use admin privileges when installing py.exe"
   (the `py` launcher) checked. Adding Python to PATH is optional.

2. **Get stockscan.** On the GitHub page, *Code → Download ZIP*, and unzip
   it, e.g. to `C:\stockscan`. (Or `git clone` it there.) Make sure the
   folder contains `pyproject.toml` directly, not another `stockscan-main`
   folder inside.

3. **Open a terminal in that folder.** In File Explorer, open `C:\stockscan`,
   then type `powershell` in the address bar and press Enter. (Command
   Prompt works too; the commands are the same.)

4. **Create a virtual environment and install:**

   ```
   py -3.13 -m venv .venv
   .venv\Scripts\python.exe -m pip install --upgrade pip
   .venv\Scripts\python.exe -m pip install .
   ```

   If `py` is not found, use the full path to `python.exe` instead of
   `py -3.13`, e.g.
   `%LOCALAPPDATA%\Programs\Python\Python313\python.exe -m venv .venv`
   (in PowerShell: `& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m venv .venv`).

5. **Check the install:**

   ```
   .venv\Scripts\stockscan.exe selftest
   ```

   The last line should read `SS 0.1.0 T0 pass=9/9`. This is field test T0.
   If anything says FAIL, write down the FAIL lines.

6. **Start the app:**

   ```
   .venv\Scripts\stockscan.exe serve --data C:\stockscan-data
   ```

   Then open <http://localhost:3000/> in a browser on the same computer.
   Keep the terminal window open while using the app; Ctrl+C stops it.

   The first time, Windows Firewall may ask whether to allow Python on the
   network. Choose **Private networks** if you want to try opening the app
   from a phone on the same network; otherwise cancel. It works on this
   computer either way. To listen on this computer only, add
   `--host 127.0.0.1`.

Optional: activate the venv so you can type `stockscan` instead of
`.venv\Scripts\stockscan.exe`. In Command Prompt run
`.venv\Scripts\activate.bat`. In PowerShell run `.venv\Scripts\Activate.ps1`;
if PowerShell refuses ("running scripts is disabled"), first run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` (affects only
that window), or just keep using the full path.

To start it later: open a terminal in `C:\stockscan` and repeat step 6.

## Install on macOS or Linux

```
cd ~/stockscan                      # the downloaded / cloned folder
python3.13 -m venv .venv            # or python3.11 / python3.12
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
.venv/bin/stockscan selftest
.venv/bin/stockscan serve --data ~/stockscan-data
```

Then open <http://localhost:3000/>.

## Upgrade to a new release

Download the new release into the same folder (replace the files; keep
`.venv`), then:

```
.venv\Scripts\python.exe -m pip install .        (Windows)
.venv/bin/python -m pip install .                (macOS / Linux)
```

and run `stockscan selftest` again. The data directory is not touched by
upgrades. After upgrading, *Re-run* (below) re-processes all earlier photo
sets with the new version.

## Offline install

If the site computer cannot reach PyPI, download the packages on a computer
that can, for the site's platform and Python version, e.g. for Windows with
Python 3.13:

```
python -m pip download --only-binary=:all: --platform win_amd64 --python-version 3.13 -d wheels . "setuptools>=77" colorama
```

(`colorama` is listed by hand because pip decides Windows-only dependencies
from the computer it runs on, not from `--platform`.)

Copy the `wheels` folder and the stockscan folder to the site computer, then
in step 4 use:

```
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels .
```

## Using it

The web app is the main interface. In short:

1. **Targets** page: generate and print the calibration board, the size-test
   sheet, front marker rows (for the front face of the shelf beams) and
   ChArUco boards (for shelf surfaces, uprights and panels). Sizes take a
   unit (`10cm`, `80mm`, `3.15in`); the defaults are on the **Settings** page,
   so once the size test has shown what works, change them there. Page sizes
   go from letter to A0 / ARCH E posters, or any `WxH`. Print at 100 %
   ("actual size"), on matte paper. Mount flat and rigid. Every row and board
   has its own serial number and marker IDs: print each PDF once.
2. **Settings** page: enter the measured length of both scale bars of each
   print (field test T1).
3. **New set** for each test, upload photos (drag and drop, or the file
   picker), or copy photos into the set's `photos` folder and press *Rescan
   folder*.
4. Run the analyses offered on the set page. Each writes a report with a
   one-line summary such as
   `SS 0.1.0 T5M n=12 w_med=0.02 w_max=0.06 h_med=0.03 h_max=0.07`.
5. The **dashboard** collects today's summary lines with a *Copy all*
   button. Only these lines (and a description of failures) need to come
   back from the site.

The field tests for the first day (T0–T8) and what to read back are listed
in [doc/design1.md, "Field tests for the first day"](doc/design1.md#field-tests-for-the-first-day).

Markers: stockscan uses its own dictionary of 20 000 8×8 markers, every
two of which differ in at least 17 cells in every rotation. A marker with up
to 6 misread cells is still read correctly, and up to 10 misread cells never
turn it into a different marker. `stockscan dictionary check` re-verifies
this and prints the details.

Camera settings: save JPEG (iPhone: Settings → Camera → Formats → Most
Compatible), main 1× lens, no zoom, macro switching off, no portrait mode.

## Data directory

All photos, hand measurements, settings, calibrations and reports are plain
files in the data directory given with `--data` (default: `stockscan-data`
in your home folder). Keep it **outside** the program folder. It stays at the
site: never copy it into the repository or send it anywhere.

```
DATA/site.yaml       settings (see examples/site.example.yaml)
DATA/cameras/        camera calibrations
DATA/targets/        generated PDFs and their geometry
DATA/sets/<set>/     set.yaml, photos/, truth.csv, clicks.yaml
DATA/reports/<set>/  one folder per analysis run
DATA/cache/          thumbnails, straight-on images; safe to delete
```

## Command line

| Command | Purpose |
|---|---|
| `stockscan serve [--data DIR] [--host H] [--port P] [--password PW]` | Run the web app (default port 3000, all network interfaces) |
| `stockscan selftest [--no-web]` | Synthetic tests; check the install |
| `stockscan rerun [--data DIR]` | Re-run all photo sets with this version; print all summary lines |
| `stockscan dictionary check [--mc N]` | Re-verify the marker dictionary and print its error-correction guarantees |

On the network, anyone who can reach the app can upload and delete.
`--password` requires a password (any user name) in the browser.

## Development

```
python3.13 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
```

Tests use synthetic images only. Never add site photos or data to the
repository.

## License

[AGPL-3.0](LICENSE).
