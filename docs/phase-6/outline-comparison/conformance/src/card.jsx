import { twice } from "./math.mjs";

export function Card({ count }) {
  return <label for="n" class="card">{twice(count)}</label>;
}

export const List = ({ items }) => (
  <ul class="list">{items.map((i) => <li key={i}>{i}</li>)}</ul>
);

export default function App() {
  return <Card count={1} />;
}
