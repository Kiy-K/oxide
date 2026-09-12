import { Level } from './types';
import * as ns from './ns';

export class Base {
  constructor(n) {
    this.n = n;
  }

  area() {
    return this.n;
  }
}

export class Derived extends ns.Base {
  async run() {
    await this.area();
  }

  area() {
    function helper() {
      return compute();
    }
    return helper();
  }
}

export const scale = (x) => x * compute();

export function compute() {
  return Level.Low;
}
