export function add(a, b) { return a + b; }
export class Counter {
  constructor(value = 0) { this.value = value; }
  increment = () => this.value++;
  get total() { return this.value; }
}
export const twice = n => add(n, n);
