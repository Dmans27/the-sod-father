#!/bin/sh
# Xcode Cloud runs this right after it clones the repo, before it resolves Swift packages.
# The Capacitor plugins (haptics, preferences, share, splash-screen, geolocation, push-notifications) are Swift packages that live in
# capacitor-app/node_modules, and the app's capacitor.config.json and public/ folder are made by
# `cap sync` -- none of that is in git, so build them here: install Node, the npm packages, then sync.
set -e

export HOMEBREW_NO_AUTO_UPDATE=1
export HOMEBREW_NO_INSTALL_CLEANUP=1
brew install node

cd "$CI_PRIMARY_REPOSITORY_PATH/capacitor-app"
npm ci
npx cap sync ios
