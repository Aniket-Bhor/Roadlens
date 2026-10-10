import React, { useState, useEffect } from 'react';
import { StyleSheet, View, NativeModules } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { ExpoGoSimulator } from './src/components/ExpoGoSimulator';

export default function App() {
  const [NativeCameraComp, setNativeCameraComp] = useState<React.ComponentType | null>(null);

  useEffect(() => {
    // Check if the native VisionCamera C++/Java binary module is present
    // NativeModules.CameraView is only present in standalone APK / Expo Dev Client,
    // and is undefined inside generic Expo Go.
    if (NativeModules.CameraView != null) {
      try {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const { RoadCamera } = require('./src/components/RoadCamera');
        setNativeCameraComp(() => RoadCamera);
      } catch (err) {
        console.warn('Could not load native RoadCamera:', err);
      }
    }
  }, []);

  return (
    <View style={styles.container}>
      <StatusBar style="light" />
      {NativeCameraComp ? <NativeCameraComp /> : <ExpoGoSimulator />}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#050A14',
  },
});
