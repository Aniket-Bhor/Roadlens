const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);

// Enable bundling of native binary .tflite weights assets
config.resolver.assetExts.push('tflite');

module.exports = config;
