import { Shape, Level } from './types';
import * as ns from './ns';

export class Base implements Shape {
  constructor(private readonly n: number) {}

  area(): number {
    return this.n;
  }
}

export class Derived extends ns.Base implements Shape {
  async run(): Promise<void> {
    await this.area();
  }

  area(): number {
    function helper() {
      return compute();
    }
    return helper();
  }
}

export function compute(): number {
  return Level.Low;
}
