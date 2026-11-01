import { twice } from './math.mjs';
export function Card({ count }) {
  return <section><strong>{twice(count)}</strong></section>;
}
export default () => <Card count={2} />;
