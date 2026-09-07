# Calibre plugin. Anna's Archive and Library Genesis

Search and download books from Library Genesis and Anna's Archive. The world's largest shadow library, aggregating Libgen, Z-Library, Sci-Hub, Internet Archive, and more.
> Anna's Archive is fully behind Cloudflare/DDoS-Guard on all public mirrors, blocking automated HTTP access. The plugin backend was switched to Library Genesis in v0.4.0. Reintegrating AA is on the roadmap if a viable path emerges.
<img width="789" height="463" alt="image" src="https://github.com/user-attachments/assets/6efde1a5-c93d-4c8c-8783-efefe4f58b24" />

---

## Features
- Search Library Genesis with 25+ results per page

## Installation

1. Go to the [latest release](../../releases/latest) and download `cal-libgen.zip`
2. In Calibre: **Preferences > Plugins > Load plugin from file**
3. Select the downloaded `.zip`, no extraction needed
4. Restart Calibre

---

## Usage

Open the store via **Store > Search stores** or the store icon in the toolbar. Search by title, author, or keyword. Use the filters in **Configure** to narrow by language, filetype, source, etc.

---

### Search filters

For checkbox options (filetype, language, source, content, access): if no boxes are checked, the filter is disabled and all results are shown. If any box is checked, only results matching that selection are returned.

---

## Building from source

```bash
git clone https://github.com/a-peirogon/cal-annas.git
cd cal-annas
zip cal-libgen.zip \
    __init__.py annas_archive.py metadata.py postimport.py \
    config.py constants.py \
    plugin-import-name-store_annas_archive.txt
calibre-customize -a cal-libgen.zip
```
