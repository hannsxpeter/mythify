#!/usr/bin/env node
'use strict';

// Packaging entry for `npx @hxp-tools/mythify`.
// No args / install flags -> scripts/install_user.sh
// Anything else -> python3 scripts/mythify.py (the real CLI)

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const INSTALLER = path.join(ROOT, 'scripts', 'install_user.sh');
const CLI = path.join(ROOT, 'scripts', 'mythify.py');
const PKG = require('../package.json');

const COLORS = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  dim: '\x1b[2m',
  cyan: '\x1b[36m',
  green: '\x1b[32m',
  red: '\x1b[31m',
};

const INSTALL_FLAGS = new Set([
  '--project',
  '--prefix',
  '--skills-root',
  '--skip-skills',
  '--skip-mcp',
  '--uninstall',
  '--install',
]);

function c(color, text) {
  return COLORS[color] + text + COLORS.reset;
}

function usage() {
  console.log(`
${c('bold', 'mythify')} ${c('dim', 'v' + PKG.version)}
Prove done. Plans close only on a passing check.

${c('bold', 'Install')}
  npx @hxp-tools/mythify@latest
  npx @hxp-tools/mythify@latest --project .
  npx @hxp-tools/mythify@latest --uninstall

${c('bold', 'Run the CLI from the package')}
  npx @hxp-tools/mythify@latest status
  npx @hxp-tools/mythify@latest plan create "GOAL" --steps '...'

${c('bold', 'Requirements')}
  Python 3.9+ and a POSIX host with sh for the installer.
`);
}

function wantsInstall(args) {
  if (args.length === 0) {
    return true;
  }
  if (args.includes('--install')) {
    return true;
  }
  return args.some(function (arg) {
    const name = arg.split('=', 1)[0];
    return INSTALL_FLAGS.has(name);
  }) && !args.some(function (arg) {
    return arg === 'status' || arg === 'plan' || arg === 'verify' || arg === 'step'
      || arg === 'route' || arg === 'init' || arg === 'campaign' || arg === 'map'
      || arg === 'outcome' || arg === 'review' || arg === 'memory' || arg === 'lesson'
      || arg === 'report' || arg === 'summary' || arg === 'history' || arg === 'reflect'
      || arg === 'protocol' || arg === 'prompt' || arg === 'loop-fit' || arg === 'logs'
      || arg === 'lineage' || arg === 'trial' || arg === 'mcp';
  });
}

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: ROOT,
    stdio: 'inherit',
    env: process.env,
  });
  if (result.error) {
    console.error(c('red', String(result.error.message || result.error)));
    return 1;
  }
  return result.status == null ? 1 : result.status;
}

function main(argv) {
  const args = argv.slice(2);
  if (args.includes('--help') || args.includes('-h')) {
    usage();
    return 0;
  }
  if (args.includes('--version') || args.includes('-v')) {
    console.log(PKG.version);
    return 0;
  }

  if (wantsInstall(args)) {
    if (process.platform === 'win32') {
      console.error(c('red', 'mythify installer does not support Windows.'));
      console.error('Clone the repository on a POSIX host and run ./scripts/install_user.sh.');
      return 2;
    }
    if (!fs.existsSync(INSTALLER)) {
      console.error(c('red', 'scripts/install_user.sh missing from package.'));
      return 2;
    }
    const installArgs = args.filter(function (arg) {
      return arg !== '--install';
    });
    console.log(c('cyan', 'mythify') + ' ' + c('dim', 'v' + PKG.version));
    console.log(c('dim', 'Installing the CLI and skills...'));
    console.log('');
    const code = run('sh', [INSTALLER].concat(installArgs));
    if (code === 0) {
      console.log('');
      console.log(c('green', 'Next: copy AGENTS.md into your project, then run mythify status.'));
      console.log(c('dim', 'Docs: ' + (PKG.homepage || 'https://github.com/hannsxpeter/mythify')));
    }
    return code;
  }

  if (!fs.existsSync(CLI)) {
    console.error(c('red', 'scripts/mythify.py missing from package.'));
    return 2;
  }
  return run('python3', [CLI].concat(args));
}

process.exitCode = main(process.argv);
