// Packaging targets/metadata follow upstream NovaAgent's electron-builder.thin.config.mjs
// (deb + AppImage, Windows NSIS); the app itself is our SSO shell in ./app.
const { version } = require('./app/package.json')

module.exports = {
  appId: 'local.isigpu.nova.desktop',
  productName: 'NovaAgent',
  copyright: `Copyright © ${new Date().getFullYear()} NovaAgent`,
  directories: { app: 'app', output: 'dist', buildResources: 'build-resources' },
  asar: true,
  npmRebuild: false,
  files: ['main.js', 'preload.js', 'offline.html', 'package.json', 'icon.png', 'isigpu-root-ca.crt', 'vazirmatn-*.woff2'],
  win: {
    target: [{ target: 'nsis', arch: ['x64'] }],
    icon: 'icon.ico',
    // no Authenticode cert here; rcedit (icon/version info) runs under wine in the builder image
    signAndEditExecutable: process.env.NOVA_WIN_EDIT_EXE !== '0'
  },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: 'NovaAgent',
    installerIcon: 'icon.ico',
    uninstallerIcon: 'icon.ico',
    installerHeaderIcon: 'icon.ico',
    artifactName: 'NovaAgent-Setup-${version}-x64.${ext}'
  },
  linux: {
    maintainer: 'NovaAgent <nova@isigpu.local>',
    vendor: 'NovaAgent',
    target: [{ target: 'deb', arch: ['x64'] }, { target: 'AppImage', arch: ['x64'] }],
    category: 'Development',
    synopsis: 'NovaAgent coding agent',
    description: 'NovaAgent desktop: signs in with your organisation account; agents and workspaces run on the NovaAgent server.',
    icon: 'icons',
    executableName: 'novaagent',
    desktop: { entry: { Name: 'NovaAgent', Comment: 'AI coding agent', Categories: 'Development;IDE;', StartupWMClass: 'NovaAgent' } },
    artifactName: 'NovaAgent-${version}-${arch}.${ext}'
  },
  deb: {
    depends: ['libgtk-3-0t64 | libgtk-3-0', 'libnotify4', 'libnss3', 'libxss1', 'libxtst6', 'libasound2t64 | libasound2', 'xdg-utils', 'libuuid1'],
    artifactName: 'NovaAgent-${version}-amd64.${ext}'
  },
  extraMetadata: { version }
}
