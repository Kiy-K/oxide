export interface Shape {
  area(): number;
}

export type Handler = (x: number) => void;

export enum Level {
  Low,
  High,
}

export const DEFAULT_TIMEOUT = 30;
