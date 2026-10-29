#!/usr/bin/env bash
set -euo pipefail

# Build an unsigned macOS .app and a ZIP containing the app plus the same
# Skill/docs payload used by the Windows release. Run on macOS with Rust and
# the requested target installed; this script never claims signing/notarization.
project_root="$(cd "$(dirname "$0")/.." && pwd)"
target="${1:-$(rustc -vV | awk '/host:/{print $2}')}"
wheelhouse="${3:-}"
version="$(awk -F'"' '/^version[[:space:]]*=/{print $2; exit}' "$project_root/desktop/Cargo.toml")"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"
command -v cargo >/dev/null || { echo "cargo is required" >&2; exit 10; }
command -v rsync >/dev/null || { echo "rsync is required" >&2; exit 10; }
command -v ditto >/dev/null || { echo "ditto is required" >&2; exit 10; }
command -v hdiutil >/dev/null || { echo "hdiutil is required" >&2; exit 10; }
command -v codesign >/dev/null || { echo "codesign is required" >&2; exit 10; }
[[ -f "$project_root/scripts/setup_macos.py" ]] || { echo "setup_macos.py is required" >&2; exit 10; }
case "$target" in
  aarch64-apple-darwin) ;;
  *) echo "Only Apple Silicon (aarch64-apple-darwin) is supported" >&2; exit 2 ;;
esac

out="${2:-$project_root/dist/hr-resume-macos-$target}"
if [[ -e "$out" || -e "$out.zip" || -e "$out.dmg" ]]; then
  echo "output already exists: $out" >&2
  exit 3
fi
cargo build --locked --release --target "$target" --manifest-path "$project_root/desktop/Cargo.toml"
exe="$project_root/desktop/target/$target/release/hr-resume-viewer"
[[ -x "$exe" ]] || { echo "release executable not found: $exe" >&2; exit 4; }

app="$out/招聘看板.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp "$exe" "$app/Contents/MacOS/招聘看板"
chmod 755 "$app/Contents/MacOS/招聘看板"
cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleDisplayName</key><string>招聘工作台</string>
<key>CFBundleExecutable</key><string>招聘看板</string>
<key>CFBundleIdentifier</key><string>com.tukjet.hrresume</string>
<key>CFBundleName</key><string>招聘工作台</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>$version</string>
<key>CFBundleVersion</key><string>$version</string>
<key>LSMinimumSystemVersion</key><string>$MACOSX_DEPLOYMENT_TARGET</string>
</dict></plist>
PLIST
build_sha="${GITHUB_SHA:-local}"
build_info="{\"version\":\"$version\",\"target\":\"$target\",\"git_sha\":\"$build_sha\",\"signed\":\"ad-hoc\",\"notarized\":false}"
printf '%s\n' "$build_info" > "$out/BUILD-INFO.json"
printf '%s\n' "$build_info" > "$app/Contents/Resources/BUILD-INFO.json"
cp "$project_root/README.md" "$out/"
cp "$project_root/使用说明.html" "$out/"
cp -R "$project_root/docs" "$out/"
mkdir -p "$out/skills/manage-hr-resumes"
rsync -a --exclude='__pycache__' --exclude='.venv' --exclude='.update' --exclude='workspace.local.json' \
  "$project_root/skills/manage-hr-resumes/" "$out/skills/manage-hr-resumes/"
mkdir -p "$out/scripts"
cp "$project_root/scripts/setup_macos.py" "$out/scripts/"
if [[ -n "$wheelhouse" ]]; then
  mkdir -p "$out/wheelhouse"
  find "$wheelhouse" -maxdepth 1 -type f -name '*.whl' -exec cp {} "$out/wheelhouse/" \;
fi
# Sign the finished bundle with an ad-hoc identity so nested bundle metadata is
# internally consistent. This is not a Developer ID signature and is not
# notarized; Gatekeeper trust still requires a future Developer ID release.
codesign --force --deep --sign - "$app"
(cd "$(dirname "$out")" && ditto -c -k --sequesterRsrc --keepParent "$(basename "$out")" "$(basename "$out").zip")
dmg_stage="$(mktemp -d "${TMPDIR:-/tmp}/hr-resume-dmg.XXXXXX")"
trap 'rm -rf "$dmg_stage"' EXIT
cp -R "$app" "$dmg_stage/招聘看板.app"
ln -s /Applications "$dmg_stage/Applications"
hdiutil create -volname "招聘工作台 $version" -srcfolder "$dmg_stage" -ov -format UDZO "$out.dmg" >/dev/null
printf '{"target":"%s","directory":"%s","zip":"%s","dmg":"%s","signed":"ad-hoc","notarized":false}\n' "$target" "$out" "$out.zip" "$out.dmg"
