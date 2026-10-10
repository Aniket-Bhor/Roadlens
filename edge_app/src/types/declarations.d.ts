/**
 * RoadLens Scout - Asset & Global Declarations
 */
declare module '*.tflite' {
  const value: number;
  export default value;
}

declare namespace NodeJS {
  interface ProcessEnv {
    [key: string]: string | undefined;
  }
  interface Process {
    env: ProcessEnv;
  }
}

declare const process: NodeJS.Process;
