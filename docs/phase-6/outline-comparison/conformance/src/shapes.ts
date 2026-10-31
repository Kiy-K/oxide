// Outline conformance: TypeScript shapes.
import { a, b as c } from "./a";
import type { T } from "./types";
import * as ns from "./ns";
import def, {
  x,
  y,
} from "./multi";

export const LIMIT = 10;
const local = (n: number): number => n + 1;

export function parse(input: string): T;
export function parse(input: Buffer): T;
export function parse(input: string | Buffer): T {
  return input as unknown as T;
}

export interface Shape {
  area(): number;
  readonly name: string;
}

export type Id = string | number;

export enum Color {
  Red,
  Green = "green",
}

export abstract class Base<K> implements Shape {
  static count = 0;
  readonly name = "base";
  #secret = 1;
  constructor(private readonly key: K) {}
  abstract area(): number;
  get label(): string {
    return this.name;
  }
  async refresh(this: Base<K>, force?: boolean): Promise<void> {}
}

export namespace Geometry {
  export function unit(): number {
    return 1;
  }
}

declare module "external" {
  export function ext(): void;
}

export default class Widget extends Base<string> {
  area(): number {
    return 0;
  }
}

export { local as increment };
export * from "./reexport";
