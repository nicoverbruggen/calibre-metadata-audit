# Metadata Audit for Calibre

Compare the metadata in your Calibre library with the metadata inside its EPUB files. Review the differences and choose which fields to copy from Calibre into each EPUB.

## What it checks

- Title.
- Authors, including conflicting additional creator fields.
- Series name and number.
- Publisher.

A difference is not proof of a mistake. The plugin treats Calibre's database as the source for updates. It does not look up books online or decide whether a title, author or series number is factually correct. Correct any database mistakes in Calibre before applying them to EPUBs.

## Installation

1. Download an installable `Metadata-Audit-<version>.zip` from a GitHub release, when available. Do not use GitHub's automatic source-code ZIP.
2. In Calibre, open **Preferences → Plugins → Load plugin from file** and select the plugin ZIP.
3. Restart Calibre.
4. **Metadata Audit** appears on the main toolbar automatically on its first startup, including when a device is connected. You can move or remove it through **Preferences → Toolbars & menus**. Later restarts preserve your changes.

For a CI build, open a successful **Build plugin** workflow run in GitHub Actions and download the **Metadata-Audit** artifact. Extract the downloaded artifact once to get the installable plugin ZIP inside. Keep that inner ZIP intact when installing it in Calibre.

The plugin has been tested with Calibre 9.17.0 on macOS. It declares Calibre 7.0 as its minimum version, but older Calibre versions, Windows and Linux have not been tested.

## Use

1. Open **Metadata Audit** from the toolbar.
2. Scan selected books or the whole library.
3. Compare the **Calibre database** and **Inside EPUB** columns. Hover over a value to read it in full.
4. Check the individual fields you want to change. Nothing is checked by default.
5. Click **Apply checked differences** and confirm the changes.
6. Scan again to check the updated files.

Applying runs in the background and shows progress. **Stop after current book** keeps completed changes and skips the remaining books. Closing the dialog during an apply also waits for the current book to finish.

## File handling

The plugin updates the current EPUB stored in Calibre. It does not update separate exported copies or books already on a device. It does not change Calibre's title, author, series or publisher values.

Only the EPUB package metadata is rewritten. Covers, illustrations and story files are preserved. Explicit translator and illustrator roles are preserved. The plugin checks the rewritten archive and verifies that every other archive entry is unchanged before saving it through Calibre's database API.

No `ORIGINAL_EPUB` backup is created. Existing backups are left alone and are not used as the source. Keep your own library backup or try the plugin on a disposable library first.

If the EPUB or its Calibre metadata changes after a scan, the plugin refuses that update and asks you to scan again. Per-book failures are available through **Show scan errors**. Other selected books can still be processed.

## Repository layout

```text
plugin/     Plugin entry point, metadata handling and UI
tests/     Metadata, UI and background-write tests
tools/     ZIP build script
.github/   GitHub Actions workflow
dist/      Generated installable ZIPs, ignored by Git
```

The build maps the files in `plugin/` to the ZIP root. The repository layout does not add an extra directory inside the installable plugin.

## Build

The build needs Python 3.10 or newer. It uses only the Python standard library and does not import or run Calibre.

```sh
python3 tools/build_plugin.py
```

The installable ZIP is written to `dist/`. The version comes from `plugin/__init__.py`. The archive includes only the plugin modules, its import marker, this README and the license. Tests, books, local settings and caches are excluded.

GitHub Actions runs the same build on every push, pull request and manual run, then uploads the ZIP as a workflow artifact. Pushing a version tag such as `v0.2.0` also publishes a GitHub Release with that ZIP attached and generated release notes. The tag must match the version in `plugin/__init__.py`; a mismatch stops the release. Other tags only build the plugin. The workflow checks packaging and Python syntax; it does not run the Calibre integration tests.

To release a version, update the plugin version, commit the change and push it. Then create and push the matching tag:

```sh
git tag -a v0.2.0 -m "Release 0.2.0"
git push origin v0.2.0
```

Use the new version number in both commands for later releases. Creating a local tag does not publish a release. Pushing the tag triggers publication after the build succeeds.

The tests in `tests/` require Calibre's Python runtime. The UI tests also require the plugin to be installed in an isolated Calibre settings profile. Run them only with a disposable profile. The database integration test creates its own temporary library; it does not use your existing libraries.

## License

Copyright © 2026 Nico Verbruggen.

Licensed under GNU GPL version 3 only (`GPL-3.0-only`). See [LICENSE](LICENSE) for the full terms.
